import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep Turbopack scoped to this project (the repo root is the home directory).
  turbopack: {
    root: path.join(__dirname, "."),
  },
};

export default nextConfig;
