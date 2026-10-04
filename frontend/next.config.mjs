/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The dev indicator is a floating button pinned to the bottom-left, which is
  // exactly where the sidebar's theme toggle sits. It covers it: unclickable in
  // a browser test and awkward in real use. Turned off rather than working
  // around it, because the overlay is not telling us anything the terminal is
  // not already saying.
  devIndicators: false,
  // The API base is read at request time on the server and baked in for the
  // client, so the same image runs against local, staging and production.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1",
  },
};

export default nextConfig;
