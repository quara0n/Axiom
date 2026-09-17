import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The floating dev badge sits on top of the sidebar's last button, which made
  // "New thread" look broken in development.
  devIndicators: false,
};

export default nextConfig;
