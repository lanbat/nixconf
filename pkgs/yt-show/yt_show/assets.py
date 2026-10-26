"""Pictures that ship with the helper (the media app serves them)."""

# A result with no picture of its own: the LANBAT bat, from the site's favicon,
# drawn 16:9 in the dark navy, served at /media/bat.svg on the screen's domain.
BAT_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360" role="img" aria-label="LANBAT">
  <defs>
    <radialGradient id="glow" cx="50%" cy="40%" r="70%">
      <stop offset="0" stop-color="#2b2857"/>
      <stop offset="0.65" stop-color="#1c1a3a"/>
      <stop offset="1" stop-color="#141230"/>
    </radialGradient>
  </defs>
  <rect width="640" height="360" rx="18" fill="url(#glow)"/>
  <rect x="1" y="1" width="638" height="358" rx="18" fill="none" stroke="#f2a516" stroke-opacity="0.18"/>
  <g transform="translate(320 186) scale(8) translate(-16 -16)">
    <path fill="#f2a516" d="M16 10 17.6 5.8 19.2 10.2C22.5 8.6 26.5 9.4 30 13Q27.6 13.2 26.4 16.6Q24.2 15 22.4 17.4Q21 16.6 19.6 21.4L16 26.5 12.4 21.4Q11 16.6 9.6 17.4Q7.8 15 5.6 16.6Q4.4 13.2 2 13C5.5 9.4 9.5 8.6 12.8 10.2L14.4 5.8Z"/>
  </g>
</svg>
"""
