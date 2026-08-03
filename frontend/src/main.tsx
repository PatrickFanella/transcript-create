import { lazy, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import AppLayout from './routes/AppLayout';
import { PublicAccessProvider, queryClient, ThemeProvider } from './services';
import { NotFoundPage, PageSuspense as Page, RouteErrorPage } from './routes/RouteStates';

const HomePage = lazy(() => import('./routes/HomePage'));
const SearchPage = lazy(() => import('./routes/SearchPage'));
const ExplorePage = lazy(() => import('./routes/ExplorePage'));
const StreamsPage = lazy(() => import('./routes/StreamsPage'));
const TimelinePage = lazy(() => import('./routes/TimelinePage'));
const TopicPage = lazy(() => import('./routes/TopicPage'));
const VideoPage = lazy(() => import('./routes/VideoPage'));
const FavoritesPage = lazy(() => import('./routes/FavoritesPage'));

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      {
        index: true,
        element: (
          <Page>
            <HomePage />
          </Page>
        ),
      },
      {
        path: 'search',
        element: (
          <Page>
            <SearchPage />
          </Page>
        ),
      },
      {
        path: 'explore',
        element: (
          <Page>
            <ExplorePage />
          </Page>
        ),
      },
      {
        path: 'episodes',
        element: (
          <Page>
            <StreamsPage />
          </Page>
        ),
      },
      {
        path: 'streams',
        element: (
          <Page>
            <StreamsPage />
          </Page>
        ),
      },
      {
        path: 'timeline',
        element: (
          <Page>
            <TimelinePage />
          </Page>
        ),
      },
      {
        path: 'topics/:query',
        element: (
          <Page>
            <TopicPage />
          </Page>
        ),
      },
      {
        path: 'v/:videoId',
        element: (
          <Page>
            <VideoPage />
          </Page>
        ),
      },
      {
        path: 'saved',
        element: (
          <Page>
            <FavoritesPage />
          </Page>
        ),
      },
      {
        path: 'favorites',
        element: (
          <Page>
            <FavoritesPage />
          </Page>
        ),
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <PublicAccessProvider>
          <RouterProvider router={router} />
        </PublicAccessProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>
);
