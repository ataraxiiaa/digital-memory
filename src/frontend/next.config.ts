import type { NextConfig } from "next";

const config: NextConfig = {
  // Browser tests use a separate directory without interrupting local development.
  distDir: process.env.NEXT_BUILD_DIR || ".next",
};

export default config;
