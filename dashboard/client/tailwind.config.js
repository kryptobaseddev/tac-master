/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{vue,ts,js}"],
  theme: {
    extend: {
      colors: {
        ink: {
          50: "#f5f7fa",
          100: "#e4e7eb",
          200: "#cbd2d9",
          400: "#7b8794",
          600: "#3e4c59",
          800: "#1f2933",
          900: "#12171e",
        },
        accent: {
          pending: "#f59e0b",
          running: "#3b82f6",
          succeeded: "#10b981",
          failed: "#ef4444",
          self: "#a855f7",
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
