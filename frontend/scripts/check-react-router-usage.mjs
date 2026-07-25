#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

const extensions = new Set(['.ts', '.tsx', '.js', '.jsx', '.mts', '.cts', '.mjs', '.cjs']);
const findings = [];

function isRouterModule(specifier) {
  return (
    specifier === 'react-router' ||
    specifier === 'react-router-dom' ||
    specifier.startsWith('react-router/') ||
    specifier.startsWith('react-router-dom/')
  );
}

function isAllowedNamedImport(node) {
  return (
    node.importClause &&
    !node.importClause.name &&
    node.importClause.namedBindings &&
    ts.isNamedImports(node.importClause.namedBindings)
  );
}

function isForbiddenApi(name) {
  return name.startsWith('unstable_') || /rsc|server/i.test(name);
}

function report(sourceFile, node, message) {
  const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
  findings.push(`${sourceFile.fileName}:${line + 1}: ${message}`);
}

function reportParseDiagnostic(sourceFile, diagnostic) {
  const position = diagnostic.start ?? 0;
  const { line } = sourceFile.getLineAndCharacterOfPosition(position);
  findings.push(`${sourceFile.fileName}:${line + 1}: could not parse React Router source safely`);
}

function moduleSpecifier(expression) {
  if (ts.isStringLiteral(expression)) return expression.text;
  if (ts.isNoSubstitutionTemplateLiteral(expression)) return expression.text;
  return null;
}

function isRelativeSpecifier(specifier) {
  return specifier.startsWith('./') || specifier.startsWith('../');
}

function scriptKind(fileName) {
  switch (path.extname(fileName)) {
    case '.tsx':
      return ts.ScriptKind.TSX;
    case '.jsx':
      return ts.ScriptKind.JSX;
    case '.js':
    case '.mjs':
    case '.cjs':
      return ts.ScriptKind.JS;
    default:
      return ts.ScriptKind.TS;
  }
}

function inspectFile(fileName) {
  const sourceFile = ts.createSourceFile(
    fileName,
    fs.readFileSync(fileName, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
    scriptKind(fileName)
  );
  const parseError = sourceFile.parseDiagnostics[0];
  if (parseError) {
    reportParseDiagnostic(sourceFile, parseError);
    return;
  }
  const checkNamed = (elements) => {
    for (const element of elements) {
      const original = (element.propertyName ?? element.name).text;
      if (isForbiddenApi(original))
        report(sourceFile, element, `forbidden React Router API ${original}`);
    }
  };
  const visit = (node) => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const specifier = node.moduleSpecifier.text;
      if (isRouterModule(specifier)) {
        if (specifier !== 'react-router-dom' || !isAllowedNamedImport(node)) {
          report(sourceFile, node, 'forbidden React Router package or import form');
        } else {
          checkNamed(node.importClause.namedBindings.elements);
        }
      }
    } else if (
      ts.isExportDeclaration(node) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      const specifier = node.moduleSpecifier.text;
      if (isRouterModule(specifier)) {
        if (
          specifier !== 'react-router-dom' ||
          !node.exportClause ||
          !ts.isNamedExports(node.exportClause)
        ) {
          report(sourceFile, node, 'forbidden React Router package or re-export form');
        } else {
          checkNamed(node.exportClause.elements);
        }
      }
    } else if (ts.isImportEqualsDeclaration(node)) {
      const moduleReference = node.moduleReference;
      if (ts.isExternalModuleReference(moduleReference)) {
        const specifier = moduleSpecifier(moduleReference.expression);
        if (specifier === null || isRouterModule(specifier)) {
          report(sourceFile, node, 'forbidden React Router import-equals form');
        }
      }
    } else if (ts.isCallExpression(node)) {
      const argument = node.arguments[0];
      const isDynamicImport = node.expression.kind === ts.SyntaxKind.ImportKeyword;
      const isRequire = ts.isIdentifier(node.expression) && node.expression.text === 'require';
      const specifier = argument && moduleSpecifier(argument);
      if (
        (isDynamicImport && (specifier === null || isRouterModule(specifier) || !isRelativeSpecifier(specifier))) ||
        (isRequire && (specifier === null || isRouterModule(specifier)))
      ) {
        report(sourceFile, node, 'forbidden React Router dynamic import or require');
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
}

function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) walk(entryPath);
    else if (entry.isFile() && extensions.has(path.extname(entry.name))) inspectFile(entryPath);
  }
}

try {
  if (process.argv.length !== 3 || !fs.statSync(process.argv[2]).isDirectory())
    throw new Error('expected one source directory');
  walk(process.argv[2]);
  process.stdout.write(`${JSON.stringify({ findings })}\n`);
} catch (error) {
  process.stderr.write(
    `React Router AST checker failed: ${error instanceof Error ? error.message : String(error)}\n`
  );
  process.exitCode = 2;
}
