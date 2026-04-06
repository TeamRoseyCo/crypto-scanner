module.exports = {
    content: [
      "./app/**/*.{js,ts,jsx,tsx,mdx}",
      "./component/**/*.{js,ts,jsx,tsx,mdx}",
      "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    ],
    theme: {
      extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
      },
    },
  },
        plugins: [require("daisyui")],
  };