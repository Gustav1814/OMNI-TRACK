/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        dark: {
          bg: '#0a0a0a',
          card: '#141414',
          border: 'rgba(255,255,255,0.05)',
        },
        light: {
          bg: '#f8f9fa',
          card: '#ffffff',
        },
      },
    },
  },
  plugins: [],
}
