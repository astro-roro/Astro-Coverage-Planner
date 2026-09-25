// Visibility wording for a target: this month's label, the trend over the
// next quarter, and the compact chip the target list shows in place of the
// separate NOW and TREND pills. Pure, so `node --test` can check the text.

export const MONTH_LABELS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
export const LABEL_RANK = { not_visible: 0, partial: 1, fair: 2, good: 3, great: 4 };
export const LABEL_PRETTY = {
  not_visible: "Not visible",
  partial: "Partial",
  fair: "Fair",
  good: "Good",
  great: "Great",
};

// "Not visible" splits into two real states the user cares about: target
// genuinely never rises (peak < 0°) vs target rises but doesn't clear the
// site's min-altitude (0° ≤ peak < min). Same bin label internally, only
// the display copy changes.
export function prettyLabel(label, peak_alt_deg) {
  if (label !== "not_visible") return LABEL_PRETTY[label];
  if (peak_alt_deg == null || peak_alt_deg < 0) return "Below horizon";
  return `Below min (peaks ${peak_alt_deg}°)`;
}
export function prettyLabelShort(label, peak_alt_deg) {
  if (label !== "not_visible") return LABEL_PRETTY[label];
  if (peak_alt_deg == null || peak_alt_deg < 0) return "Below horizon";
  return `Max ${peak_alt_deg}°`;
}

function binFor(bins, month1) {
  return bins.find(x => x.month === month1) || null;
}

// The next quarter against this month. `kind` picks the colour class
// (nn-trend-<kind>), `arrow` is what the compact chip shows, `text` is the
// full wording for the detail panel.
export function trendOf(bins, nowMonth) {
  if (!bins) return null;
  const nowBin = binFor(bins, nowMonth);
  if (!nowBin) return null;
  const nowRank = LABEL_RANK[nowBin.label] ?? 0;

  // 3-month lookahead average vs current rank, simple heuristic for
  // "is the next quarter better/worse/the same".
  const nextRanks = [];
  for (let i = 1; i <= 3; i++) {
    const b = binFor(bins, ((nowMonth - 1 + i) % 12) + 1);
    if (b) nextRanks.push(LABEL_RANK[b.label] ?? 0);
  }
  if (!nextRanks.length) return null;
  const diff = nextRanks.reduce((a, b) => a + b, 0) / nextRanks.length - nowRank;

  if (diff > 0.5) {
    return { kind: "up", arrow: "↑", text: "↑ Improving",
             title: "3-month forward avg rank is higher than current month." };
  }
  if (diff < -0.5) {
    return { kind: "down", arrow: "↓", text: "↓ Declining",
             title: "3-month forward avg rank is lower than current month." };
  }
  // Steady. If we're currently in a poor state (rank < good=3), surface
  // when the next decent month arrives instead of the uninformative
  // "Steady", that's actually the more actionable signal.
  if (nowRank < 3) {
    for (let i = 1; i <= 12; i++) {
      const b = binFor(bins, ((nowMonth - 1 + i) % 12) + 1);
      if (b && (LABEL_RANK[b.label] ?? 0) >= 3) {
        return { kind: "wait", arrow: `↗ ${i}m`, text: `Peaks in ${i}m`,
                 title: "First Good-or-better month in the year ahead." };
      }
    }
    return { kind: "flat", arrow: "→", text: "Stays low",
             title: "Stays poor across the year ahead." };
  }
  return { kind: "flat", arrow: "→", text: "→ Steady",
           title: "3-month forward rank ≈ current." };
}

// The one chip a target row carries: this month's label plus the trend
// arrow, e.g. "Great ↓" or "Max 14.6° →". The title and accessible label
// carry the full NOW and TREND wording the row no longer shows.
export function compactChip(bins, nowMonth, minAlt = 30) {
  if (!bins) return null;
  const b = binFor(bins, nowMonth);
  if (!b) return null;
  const label = prettyLabelShort(b.label, b.peak_alt_deg);
  const trend = trendOf(bins, nowMonth);
  const peak = b.peak_alt_deg == null ? "none" : `${b.peak_alt_deg}°`;
  const nowFull = `Now (${MONTH_LABELS[nowMonth - 1]}): ${prettyLabel(b.label, b.peak_alt_deg)}, peak ${peak}, ${b.hours_above_min}h above ${minAlt}°`;
  const lines = [nowFull];
  if (trend) lines.push(`Trend: ${trend.text}. ${trend.title}`);
  return {
    labelClass: `nn-${b.label}`,
    label,
    trendKind: trend?.kind ?? null,
    arrow: trend?.arrow ?? "",
    text: trend ? `${label} ${trend.arrow}` : label,
    title: lines.join("\n"),
    ariaLabel: lines.join(". "),
  };
}
