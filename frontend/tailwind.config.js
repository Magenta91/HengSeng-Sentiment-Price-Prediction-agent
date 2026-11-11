/** Palette note: up/down use blue/amber (not green/red) because Hong Kong and Korean platforms follow
 *  opposite colour conventions; arrows carry the meaning too, so colour is never the only signal. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ground: "#ECEFF3", surface: "#FFFFFF", ink: "#0E1A2B", muted: "#566478", line: "#D3DAE2",
        up: "#1F4FD8", down: "#BF6408", flat: "#8190A3", demo: "#6B2FA0", demoSoft: "#F0E8F7",
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', '"Noto Sans TC"', '"Noto Sans KR"', '"Malgun Gothic"', '"PingFang HK"', "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
