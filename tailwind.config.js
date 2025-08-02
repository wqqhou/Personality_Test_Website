

export default {
  content: ["./templates/**/*.html"],
  theme: {
    extend: {
      fontFamily: {
        kinghwa: ['KingHwa_OldSong', 'serif'],
      },
      fontSize: {
        hero: ['120px', { lineHeight: '1' }], // 120px, line-height: 100%
      },
      colors: {
        'text-dark': '#0A0A0A',
      },
    },
  },
  plugins: [],
}