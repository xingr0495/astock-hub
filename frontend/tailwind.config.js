/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          900: '#0b1120',
          850: '#111a2e',
          800: '#16213a',
          700: '#1e2c4a',
          600: '#2a3a5f',
        },
        brand: {
          DEFAULT: '#38bdf8',
          dim: '#0ea5e9',
        },
      },
    },
  },
  plugins: [],
}