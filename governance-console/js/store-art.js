// Inline product illustrations for the storefront. Line art in the ink colour over a soft fill, so every
// product reads as one catalogue without shipping image files. Each entry is the inside of a 200x200 SVG.
(function () {
  const ink = 'stroke="#1f2a24" stroke-width="3.2" stroke-linejoin="round" stroke-linecap="round"';
  const thin = 'stroke="#1f2a24" stroke-width="2.2" stroke-linecap="round" fill="none"';

  const ART = {
    shoe: `
      <ellipse cx="104" cy="160" rx="78" ry="7" fill="#1f2a24" opacity=".10"/>
      <path ${ink} fill="var(--a2)" d="M26 138 Q26 152 40 153 L166 153 Q182 153 182 140 L182 132 L26 132 Z"/>
      <path ${ink} fill="var(--a1)" d="M30 132 C30 108 42 94 58 88 L84 77 Q96 72 103 84 L112 100 Q142 106 162 114 Q181 121 181 132 Z"/>
      <path ${thin} d="M78 84 L92 102 M68 90 L84 106 M88 80 L100 98"/>
      <path ${thin} stroke="#fffaf2" stroke-width="5" d="M54 122 Q100 104 150 124"/>
      <path ${thin} d="M40 142 L170 142" opacity=".45"/>`,
    bottle: `
      <ellipse cx="100" cy="182" rx="44" ry="6" fill="#1f2a24" opacity=".10"/>
      <path ${ink} fill="none" d="M88 30 Q100 14 112 30"/>
      <rect ${ink} fill="var(--a2)" x="82" y="28" width="36" height="22" rx="6"/>
      <rect ${ink} fill="var(--a2)" x="78" y="48" width="44" height="14" rx="4"/>
      <rect ${ink} fill="var(--a1)" x="62" y="60" width="76" height="118" rx="24"/>
      <rect x="62" y="104" width="76" height="22" fill="#fffaf2" opacity=".85"/>
      <path ${thin} d="M62 104 H138 M62 126 H138"/>
      <path ${thin} stroke="#fffaf2" stroke-width="4" d="M76 76 V92" opacity=".8"/>
      <path ${thin} d="M86 115 l6 -6 l6 6 l6 -6 l6 6 l6 -6"/>`,
    jacket: `
      <path ${ink} fill="var(--a1)" d="M70 48 L100 40 L130 48 L160 64 L178 140 L156 147 L147 100 L147 174 L53 174 L53 100 L44 147 L22 140 L40 64 Z"/>
      <path ${ink} fill="var(--a2)" d="M74 50 Q100 18 126 50 Q112 60 100 60 Q88 60 74 50 Z"/>
      <path ${thin} d="M100 60 V174"/>
      <path ${thin} d="M66 120 L84 116 M134 120 L116 116"/>
      <path ${thin} stroke="#fffaf2" stroke-width="4" d="M56 92 L144 92" opacity=".75"/>
      <rect x="96" y="66" width="8" height="12" rx="2" fill="#1f2a24"/>`,
    laptop: `
      <ellipse cx="100" cy="158" rx="86" ry="6" fill="#1f2a24" opacity=".10"/>
      <rect ${ink} fill="#2a3530" x="44" y="46" width="112" height="78" rx="7"/>
      <defs><linearGradient id="scr" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--a1)"/><stop offset="1" stop-color="var(--a2)"/></linearGradient></defs>
      <rect x="52" y="54" width="96" height="62" rx="3" fill="url(#scr)"/>
      <path d="M52 116 L78 88 L92 100 L112 76 L148 116 Z" fill="#1f2a24" opacity=".55"/>
      <circle cx="128" cy="70" r="6" fill="#fffaf2" opacity=".85"/>
      <path ${ink} fill="#c9c3b6" d="M30 126 L170 126 L184 146 Q186 152 178 152 L22 152 Q14 152 16 146 Z"/>
      <path ${thin} d="M86 140 H114"/>`,
    tent: `
      <path ${thin} d="M10 164 H190" opacity=".5"/>
      <path ${ink} fill="var(--a1)" d="M22 162 L100 50 L178 162 Z"/>
      <path ${ink} fill="var(--a2)" d="M100 90 L80 162 L120 162 Z"/>
      <path ${thin} d="M100 50 L100 90"/>
      <path ${thin} d="M100 50 L58 162 M100 50 L142 162" opacity=".45"/>
      <path ${thin} d="M22 162 L8 172 M178 162 L192 172"/>
      <path ${thin} d="M100 50 L100 36 M94 40 L106 40"/>`,
    parka: `
      <path ${ink} fill="var(--a1)" d="M68 46 L100 38 L132 46 Q160 56 166 76 L180 140 Q170 150 156 146 L150 108 L152 172 Q100 182 48 172 L50 108 L44 146 Q30 150 20 140 L34 76 Q40 56 68 46 Z"/>
      <path ${ink} fill="var(--a2)" d="M70 48 Q100 22 130 48 Q114 60 100 60 Q86 60 70 48 Z"/>
      <path ${thin} d="M50 84 Q100 92 150 84 M50 108 Q100 116 150 108 M50 132 Q100 140 150 132 M50 156 Q100 164 150 156"/>
      <path ${thin} d="M100 60 V176"/>
      <path ${thin} d="M32 100 Q40 104 46 102 M168 100 Q160 104 154 102 M28 122 Q36 126 44 124 M172 122 Q164 126 156 124"/>`,
    poles: `
      <path ${ink} stroke-width="5" d="M70 34 L112 172 M118 34 L150 172"/>
      <rect ${ink} fill="var(--a1)" x="60" y="22" width="20" height="40" rx="8" transform="rotate(-17 70 42)"/>
      <rect ${ink} fill="var(--a1)" x="108" y="22" width="20" height="40" rx="8" transform="rotate(-13 118 42)"/>
      <ellipse ${ink} fill="var(--a2)" cx="108" cy="158" rx="14" ry="5"/>
      <ellipse ${ink} fill="var(--a2)" cx="146" cy="158" rx="14" ry="5"/>
      <path ${thin} d="M80 72 L84 86 M126 72 L129 86" stroke="var(--a2)" stroke-width="5"/>`,
    baselayer: `
      <path ${ink} fill="var(--a1)" d="M72 42 Q100 56 128 42 L164 60 L184 138 L162 144 L146 92 L146 172 L54 172 L54 92 L38 144 L16 138 L36 60 Z"/>
      <path ${ink} fill="var(--a2)" d="M72 42 Q100 62 128 42 Q100 50 72 42 Z"/>
      <path ${thin} d="M56 158 H144" opacity=".5"/>
      <path ${thin} d="M30 126 L44 130 M170 126 L156 130" opacity=".6"/>`,
    pack: `
      <ellipse cx="100" cy="182" rx="56" ry="6" fill="#1f2a24" opacity=".10"/>
      <path ${ink} fill="var(--a1)" d="M56 64 Q56 40 100 38 Q144 40 144 64 L148 166 Q148 178 136 178 L64 178 Q52 178 52 166 Z"/>
      <path ${ink} fill="var(--a2)" d="M56 66 Q56 44 100 42 Q144 44 144 66 Q144 84 100 86 Q56 84 56 66 Z"/>
      <rect ${ink} fill="var(--a2)" x="70" y="118" width="60" height="46" rx="10"/>
      <path ${thin} d="M70 132 H130"/>
      <path ${thin} d="M84 86 V112 M116 86 V112"/>
      <path ${ink} fill="none" d="M88 40 Q100 24 112 40"/>`,
    headlamp: `
      <ellipse ${ink} fill="none" cx="100" cy="112" rx="76" ry="40" stroke="var(--a2)" stroke-width="10"/>
      <ellipse ${thin} cx="100" cy="112" rx="76" ry="40"/>
      <rect ${ink} fill="var(--a1)" x="66" y="56" width="68" height="50" rx="14"/>
      <circle ${ink} fill="#fffaf2" cx="100" cy="81" r="15"/>
      <circle cx="100" cy="81" r="6" fill="var(--a2)"/>
      <path ${thin} d="M100 40 V26 M70 46 L60 34 M130 46 L140 34" stroke="var(--a2)" stroke-width="4"/>`,
  };

  function svg(name, a1, a2, extra) {
    return `<svg viewBox="0 0 200 200" ${extra || ""} style="--a1:${a1};--a2:${a2}" aria-hidden="true">${ART[name] || ""}</svg>`;
  }

  // Dusk over three ridgelines, for the hero.
  const HERO = `
    <svg class="hero-art" viewBox="0 0 1440 620" preserveAspectRatio="xMidYMax slice" aria-hidden="true">
      <defs>
        <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#2a3b4a"/><stop offset=".45" stop-color="#8a5a52"/><stop offset=".8" stop-color="#e59a5b"/><stop offset="1" stop-color="#f3c27f"/>
        </linearGradient>
        <radialGradient id="sun" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fff2cf"/><stop offset=".6" stop-color="#ffd38a"/><stop offset="1" stop-color="#ffd38a" stop-opacity="0"/></radialGradient>
      </defs>
      <rect width="1440" height="620" fill="url(#sky)"/>
      <circle cx="1040" cy="300" r="170" fill="url(#sun)"/>
      <circle cx="1040" cy="300" r="58" fill="#fff4d6"/>
      <path fill="#a8655a" d="M0 400 L120 330 L210 372 L340 270 L470 360 L560 310 L700 392 L820 300 L940 360 L1060 280 L1200 360 L1320 316 L1440 352 V620 H0 Z"/>
      <path fill="#5d4552" d="M0 450 L90 410 L200 446 L330 360 L430 430 L540 392 L660 470 L780 380 L900 450 L1010 410 L1140 470 L1270 396 L1440 456 V620 H0 Z"/>
      <path fill="#2c3a3a" d="M0 520 L140 470 L260 512 L380 462 L520 530 L640 488 L780 540 L900 494 L1040 540 L1180 486 L1300 526 L1440 500 V620 H0 Z"/>
      <g fill="#18221f">
        <path d="M0 620 V560 Q300 520 620 566 T1440 548 V620 Z"/>
        ${[60, 96, 128, 210, 248, 1180, 1220, 1262, 1330, 1384].map((x, i) => {
          const h = 70 + ((i * 37) % 50), y = 572 - (i % 3) * 8;
          return `<path d="M${x} ${y - h} L${x - h * .28} ${y} L${x + h * .28} ${y} Z"/>`;
        }).join("")}
      </g>
    </svg>`;

  const LOGO = `<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="19" fill="#1f2a24"/><path d="M7 28 L16 14 L21 21 L25 16 L33 28 Z" fill="#f3c27f"/><path d="M16 14 L19 18.5 L16.5 18 L14 20 Z" fill="#fffaf2"/></svg>`;

  window.StoreArt = { svg, HERO, LOGO };
})();
