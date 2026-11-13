import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "../../tests/frontend/e2e",
  tsconfig: "../../tests/frontend/e2e/tsconfig.json",
  use: { baseURL: "http://127.0.0.1:3100", headless: true },
  workers: 1,
  webServer: {
    command: "npm run dev -- --hostname 127.0.0.1 --port 3100",
    url: "http://127.0.0.1:3100/sign-in",
    reuseExistingServer: false,
    env: {
      NEXT_BUILD_DIR: ".next-e2e",
      NEXT_PUBLIC_SUPABASE_URL: "https://auth-fixture.supabase.co",
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_fixture",
    },
  },
});
