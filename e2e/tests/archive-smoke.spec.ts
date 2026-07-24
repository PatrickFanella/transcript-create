import { expect, test, type Page } from "@playwright/test";

const seededVideo = {
  id: "00000000-0000-0000-0000-000000000201",
  youtube_id: "seeded-video",
  title: "Seeded archive episode",
  duration_seconds: 3661,
  state: "completed",
  uploaded_at: "2026-06-15T12:00:00Z",
  channel_name: "HasanAbi",
};

async function seedArchiveApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const respond = (body: unknown) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });

    if (url.pathname === "/api/auth/me") return respond({ user: null });
    if (url.pathname === "/api/archive/summary") {
      return respond({
        creator_name: "HasanAbi",
        video_count: 1,
        total_duration_seconds: 3661,
        transcript_word_count: 4200,
        updated_at: "2026-06-16T00:00:00Z",
        recent_videos: [seededVideo],
        popular_searches: [{ term: "labor", frequency: 12 }],
      });
    }
    if (url.pathname === "/api/archive/timeline") {
      return respond({
        buckets: [
          {
            period: "2026-06",
            label: "June 2026",
            video_count: 1,
            total_duration_seconds: 3661,
            videos: [seededVideo],
          },
        ],
      });
    }
    if (url.pathname === "/api/search/grouped") {
      return respond({
        total_moments: 1,
        total_videos: 1,
        groups: [
          {
            video: seededVideo,
            moments: [
              {
                id: 1,
                video_id: seededVideo.id,
                start_ms: 12000,
                end_ms: 18000,
                snippet: "labor rights",
                source: "whisper",
              },
            ],
          },
        ],
      });
    }
    if (url.pathname === "/api/search/suggestions")
      return respond({ suggestions: [] });
    if (url.pathname === "/api/search/mentions/export") {
      return respond({
        items: [
          {
            video_id: seededVideo.id,
            video_title: seededVideo.title,
            start_ms: 12000,
            end_ms: 18000,
            snippet: "labor rights",
            source: "whisper",
            deep_link: `/v/${seededVideo.id}?t=12`,
          },
        ],
      });
    }
    if (url.pathname === "/api/search/mention-map") {
      return respond({
        query: "labor",
        total_moments: 1,
        total_videos: 1,
        related_topics: [],
        top_episodes: [],
        top_episodes_count: 0,
      });
    }
    if (url.pathname === "/api/archive/topics/labor/timeline") {
      return respond({
        topic: "labor",
        granularity: "month",
        buckets: [
          {
            period: "2026-06",
            label: "June 2026",
            mention_count: 1,
            episode_count: 1,
            evidence: [
              {
                video: seededVideo,
                start_ms: 12000,
                end_ms: 18000,
                snippet: "labor rights",
              },
            ],
          },
        ],
      });
    }
    if (url.pathname === "/api/archive/topics/labor/opinions") {
      return respond({ items: [] });
    }
    if (url.pathname === "/api/videos") {
      return respond({
        items: [seededVideo],
        page_info: {
          has_next_page: false,
          has_previous_page: false,
          next_cursor: null,
          previous_cursor: null,
          total_count: 1,
        },
      });
    }
    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: "{}",
    });
  });
}

test.beforeEach(async ({ page }) => {
  await seedArchiveApi(page);
});

test("anonymous visitors can search from the populated archive home", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Find the moment. Read the record." }),
  ).toBeVisible();
  await expect(page.getByText("Seeded archive episode").first()).toBeVisible();

  await page.getByLabel("Search the HasanAbi archive").fill("labor rights");
  await page.getByRole("button", { name: "Search archive" }).click();
  await expect(page).toHaveURL(/\/search\?q=labor%20rights$/);
});

test("timeline links preserve the selected archive period", async ({
  page,
}) => {
  await page.goto("/timeline");
  await expect(
    page.getByRole("heading", { name: "Archive chronology" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "June 2026" })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Browse this period" }),
  ).toHaveAttribute(
    "href",
    "/episodes?date_from=2026-06-01&date_to=2026-06-30",
  );
});

test("anonymous visitors can browse the seeded VOD library", async ({
  page,
}) => {
  await page.goto("/episodes");
  await expect(page.getByText("Seeded archive episode")).toBeVisible();
  await expect(page.getByText(/1 VOD/)).toBeVisible();
});

test("every mention can become a persistent in-app playback queue", async ({
  page,
}) => {
  await page.goto("/search?q=labor");
  await page
    .getByRole("button", { name: "Add every mention to queue" })
    .click();
  await expect(page.getByText("Playback queue", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Seeded archive episode at 12s" }),
  ).toBeVisible();
});

test("topic intelligence has accessible timeline evidence and empty opinion state", async ({
  page,
}) => {
  await page.goto("/topics/labor");
  await expect(
    page.getByRole("heading", { name: "Mentions over time" }),
  ).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Accessible topic timeline data" }),
  ).toBeVisible();
  await expect(
    page.getByText("No published opinion history is available."),
  ).toBeVisible();
});
