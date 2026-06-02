/**
 * tools/write_report.js
 * Reads structured analysis JSON from stdin, writes a polished
 * Airspace Situation Report (.docx).
 *
 * Follows docx SKILL.md critical rules:
 *  - US Letter page size set explicitly (default is A4)
 *  - LevelFormat.BULLET for all bullet lists (never unicode bullets)
 *  - Dual widths on all tables: columnWidths + per-cell width
 *  - ShadingType.CLEAR (not SOLID) for shaded cells
 *  - No \n inside TextRun — separate Paragraphs only
 *  - PageBreak inside a Paragraph (not standalone)
 */

const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, HeadingLevel, AlignmentType, BorderStyle,
  WidthType, ShadingType, VerticalAlign, PageNumber,
  LevelFormat, PageBreak,
} = require("docx");
const fs   = require("fs");
const path = require("path");

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", d => raw += d);
process.stdin.on("end", () => buildReport(JSON.parse(raw)));

// ── Design tokens ────────────────────────────────────────────────────────────
const NAVY       = "1A3A5C";
const SKY_BLUE   = "2E75B6";
const LIGHT_BLUE = "D6E4F0";
const ALERT_RED  = "C00000";
const WARN_AMBER = "C55A11";
const OK_GREEN   = "375623";
const BORDER_CLR = "CCCCCC";

const CELL_BORDER = { style: BorderStyle.SINGLE, size: 1, color: BORDER_CLR };
const BORDERS     = { top: CELL_BORDER, bottom: CELL_BORDER,
                      left: CELL_BORDER, right: CELL_BORDER };

// US Letter content width (1" margins each side): 12240 - 2880 = 9360 DXA
const CONTENT_W = 9360;

// Status colours
const STATUS_COLOR = { NORMAL: OK_GREEN, ELEVATED: WARN_AMBER, CRITICAL: ALERT_RED };
const SEVERITY_COLOR = { LOW: OK_GREEN, MEDIUM: WARN_AMBER, HIGH: ALERT_RED };

// ── Helpers ──────────────────────────────────────────────────────────────────

function hCell(text, width, fill = SKY_BLUE) {
  return new TableCell({
    borders: BORDERS,
    width: { size: width, type: WidthType.DXA },
    shading: { fill, type: ShadingType.CLEAR },
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({
      children: [new TextRun({ text, bold: true, color: "FFFFFF",
                               font: "Arial", size: 20 })],
    })],
  });
}

function dCell(text, width, shade = false, color = "000000") {
  return new TableCell({
    borders: BORDERS,
    width: { size: width, type: WidthType.DXA },
    shading: shade ? { fill: "F0F5FA", type: ShadingType.CLEAR } : undefined,
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    children: [new Paragraph({
      children: [new TextRun({ text: String(text ?? "—"),
                               font: "Arial", size: 20, color })],
    })],
  });
}

function heading2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    children: [new TextRun({ text, font: "Arial", size: 28,
                              bold: true, color: SKY_BLUE })],
    spacing: { before: 280, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 4,
                        color: SKY_BLUE, space: 1 } },
  });
}

function bodyPara(text, color = "000000") {
  return new Paragraph({
    children: [new TextRun({ text, font: "Arial", size: 22, color })],
    spacing: { after: 120 },
  });
}

function bulletPara(text) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    children: [new TextRun({ text, font: "Arial", size: 22 })],
    spacing: { after: 80 },
  });
}

function spacer() {
  return new Paragraph({
    children: [new TextRun("")],
    spacing: { after: 160 },
  });
}

function kpiTable(rows) {
  // Two-column KPI table: Label | Value
  const COL = [3600, 5760];
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: COL,
    rows: rows.map(([label, value, highlight], i) =>
      new TableRow({
        children: [
          dCell(label, COL[0], i % 2 === 0, "555555"),
          dCell(value, COL[1], i % 2 === 0,
                highlight ? SEVERITY_COLOR[highlight] || "000000" : "000000"),
        ],
      })
    ),
  });
}

// ── Main builder ─────────────────────────────────────────────────────────────

function buildReport(data) {
  const {
    overall_status = "NORMAL",
    status_reason  = "",
    traffic_summary = "",
    anomalies = [],
    emergency_alerts = [],
    notable_patterns = [],
    analyst_notes = "",
    trend_analysis = {},
    _summary = {},
  } = data;

  const snap       = _summary;
  const alt        = snap.altitude_stats  || {};
  const spd        = snap.speed_stats     || {};
  const vr         = snap.vertical_rate_stats || {};
  const fetched    = snap.fetched_at ? snap.fetched_at.replace("T", " ").slice(0, 19) + " UTC" : "—";
  const generatedAt = new Date().toISOString().slice(0, 10);
  const statusColor = STATUS_COLOR[overall_status] || "000000";

  // ── Anomalies table ───────────────────────────────────────────────────────
  const ACOL = [2200, 1200, 5960];
  const anomalyTable = new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: ACOL,
    rows: [
      new TableRow({
        tableHeader: true,
        children: [
          hCell("Type",        ACOL[0]),
          hCell("Severity",    ACOL[1]),
          hCell("Description", ACOL[2]),
        ],
      }),
      ...(anomalies.length
        ? anomalies.map((a, i) => new TableRow({
            children: [
              dCell(a.type,        ACOL[0], i % 2 === 1),
              dCell(a.severity,    ACOL[1], i % 2 === 1,
                    SEVERITY_COLOR[a.severity] || "000000"),
              dCell(a.description, ACOL[2], i % 2 === 1),
            ],
          }))
        : [new TableRow({
            children: [dCell("No anomalies detected.", CONTENT_W)],
          })]
      ),
    ],
  });

  // ── Emergency alerts table ────────────────────────────────────────────────
  const ECOL = [1600, 1000, 2200, 1680, 1680, 1200];  // sum = 9360
  const emergencyTable = emergency_alerts.length ? new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: ECOL,
    rows: [
      new TableRow({
        tableHeader: true,
        children: [
          hCell("Callsign",  ECOL[0], ALERT_RED),
          hCell("Squawk",    ECOL[1], ALERT_RED),
          hCell("Meaning",   ECOL[2], ALERT_RED),
          hCell("Alt (ft)",  ECOL[3], ALERT_RED),
          hCell("Spd (kts)", ECOL[4], ALERT_RED),
        ],
      }),
      ...emergency_alerts.map((e, i) => new TableRow({
        children: [
          dCell(e.callsign  || "—", ECOL[0], i % 2 === 1, ALERT_RED),
          dCell(e.squawk    || "—", ECOL[1], i % 2 === 1, ALERT_RED),
          dCell(e.meaning   || "—", ECOL[2], i % 2 === 1),
          dCell(e.altitude_ft ?? "—", ECOL[3], i % 2 === 1),
          dCell(e.speed_kts   ?? "—", ECOL[4], i % 2 === 1),
        ],
      })),
    ],
  }) : null;

  // ── Country table ─────────────────────────────────────────────────────────
  const CCOL = [4680, 4680];
  const countries = snap.top_countries || [];
  const countryTable = new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: CCOL,
    rows: [
      new TableRow({
        tableHeader: true,
        children: [hCell("Country", CCOL[0]), hCell("Aircraft", CCOL[1])],
      }),
      ...countries.map((c, i) => new TableRow({
        children: [
          dCell(c.country, CCOL[0], i % 2 === 1),
          dCell(c.count,   CCOL[1], i % 2 === 1),
        ],
      })),
    ],
  });

  // ── Document ──────────────────────────────────────────────────────────────
  const doc = new Document({
    numbering: {
      config: [{
        reference: "bullets",
        levels: [{
          level: 0,
          format: LevelFormat.BULLET,
          text: "•",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      }],
    },
    styles: {
      default: { document: { run: { font: "Arial", size: 22 } } },
      paragraphStyles: [
        {
          id: "Heading1", name: "Heading 1",
          basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { size: 36, bold: true, font: "Arial", color: NAVY },
          paragraph: { spacing: { before: 0, after: 200 }, outlineLevel: 0 },
        },
        {
          id: "Heading2", name: "Heading 2",
          basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { size: 28, bold: true, font: "Arial", color: SKY_BLUE },
          paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1 },
        },
      ],
    },

    sections: [{
      properties: {
        page: {
          // SKILL.md: always set explicitly — docx-js defaults to A4
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            children: [
              new TextRun({ text: "AIRSPACE SITUATION REPORT  |  US West Coast",
                            font: "Arial", size: 18, color: "888888" }),
            ],
            border: { bottom: { style: BorderStyle.SINGLE, size: 4,
                                color: SKY_BLUE, space: 1 } },
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            children: [
              new TextRun({ text: `Generated ${generatedAt}  |  Page `,
                            font: "Arial", size: 18, color: "888888" }),
              new TextRun({ children: [PageNumber.CURRENT],
                            font: "Arial", size: 18, color: "888888" }),
              new TextRun({ text: " of ", font: "Arial", size: 18, color: "888888" }),
              new TextRun({ children: [PageNumber.TOTAL_PAGES],
                            font: "Arial", size: 18, color: "888888" }),
            ],
            border: { top: { style: BorderStyle.SINGLE, size: 4,
                             color: SKY_BLUE, space: 1 } },
          })],
        }),
      },

      children: [
        // ── Title block ─────────────────────────────────────────────────
        new Paragraph({
          heading: HeadingLevel.HEADING_1,
          children: [new TextRun({ text: "Airspace Situation Report",
                                   font: "Arial", size: 36, bold: true, color: NAVY })],
        }),
        new Paragraph({
          children: [
            new TextRun({ text: "Region: US West Coast (LAX / SFO / SEA)  |  ",
                          font: "Arial", size: 20, color: "555555" }),
            new TextRun({ text: `Snapshot: ${fetched}`,
                          font: "Arial", size: 20, color: "555555" }),
          ],
          spacing: { after: 80 },
        }),

        // ── Status banner ───────────────────────────────────────────────
        new Paragraph({
          children: [
            new TextRun({ text: `STATUS: ${overall_status}  `,
                          font: "Arial", size: 28, bold: true, color: statusColor }),
            new TextRun({ text: `— ${status_reason}`,
                          font: "Arial", size: 22, color: statusColor }),
          ],
          spacing: { before: 160, after: 320 },
          border: {
            top:    { style: BorderStyle.SINGLE, size: 8, color: statusColor, space: 4 },
            bottom: { style: BorderStyle.SINGLE, size: 8, color: statusColor, space: 4 },
            left:   { style: BorderStyle.SINGLE, size: 8, color: statusColor, space: 8 },
            right:  { style: BorderStyle.SINGLE, size: 8, color: statusColor, space: 8 },
          },
        }),

        spacer(),

        // ── Emergency alerts (only if present) ─────────────────────────
        ...(emergency_alerts.length ? [
          heading2("⚠ Emergency Alerts"),
          emergencyTable,
          spacer(),
        ] : []),

        // ── Traffic summary ─────────────────────────────────────────────
        heading2("Traffic Summary"),
        bodyPara(traffic_summary),
        spacer(),

        // ── Snapshot KPIs ───────────────────────────────────────────────
        heading2("Snapshot Statistics"),
        kpiTable([
          ["Total tracked",         String(snap.total_tracked ?? "—")],
          ["Airborne",              String(snap.airborne_count ?? "—")],
          ["On ground",             String(snap.ground_count ?? "—")],
          ["Altitude — min",        alt.min_ft != null ? `${alt.min_ft} ft` : "—"],
          ["Altitude — max",        alt.max_ft != null ? `${alt.max_ft} ft` : "—"],
          ["Altitude — average",    alt.avg_ft != null ? `${alt.avg_ft} ft` : "—"],
          ["Below 10,000 ft",       alt.low_count != null ? `${alt.low_count} aircraft` : "—",
                                    alt.low_count > 0 ? "MEDIUM" : null],
          ["Speed — average",       spd.avg_kts != null ? `${spd.avg_kts} kts` : "—"],
          ["Speed — max",           spd.max_kts != null ? `${spd.max_kts} kts` : "—"],
          ["Above 600 kts",         spd.fast_count != null ? `${spd.fast_count} aircraft` : "—",
                                    spd.fast_count > 0 ? "HIGH" : null],
          ["Steep climbs >2000 fpm",vr.steep_climb_count != null ? `${vr.steep_climb_count}` : "—"],
          ["Steep descents <-3000", vr.steep_descent_count != null ? `${vr.steep_descent_count}` : "—"],
        ]),
        spacer(),

        // ── Anomalies ───────────────────────────────────────────────────
        heading2("Detected Anomalies"),
        anomalyTable,
        spacer(),

        // ── Notable patterns ────────────────────────────────────────────
        heading2("Notable Patterns"),
        ...(Array.isArray(notable_patterns) && notable_patterns.length
          ? notable_patterns.map(p => bulletPara(p))
          : [bodyPara("No notable patterns identified.")]),
        spacer(),

        // ── Trend analysis ─────────────────────────────────────────────
        ...(trend_analysis.has_trend ? [
          heading2("Trend Analysis"),
          bodyPara(`Snapshot #${trend_analysis.previous_snapshot_id} → #${trend_analysis.current_snapshot_id}: ` +
            `${(trend_analysis.previous_fetched_at || "").replace("T", " ").slice(0, 19)} UTC → ` +
            `${(trend_analysis.current_fetched_at || "").replace("T", " ").slice(0, 19)} UTC`),
          bodyPara(trend_analysis.traffic_delta || ""),
          bodyPara(trend_analysis.anomaly_comparison || ""),
          ...(trend_analysis.trend_observations || []).map(p => bulletPara(p)),
          spacer(),
          bodyPara(trend_analysis.trend_narrative || ""),
          spacer(),
        ] : []),

        // ── Analyst notes ───────────────────────────────────────────────
        heading2("Analyst Notes"),
        bodyPara(analyst_notes),
        spacer(),

        // ── Country breakdown (new page) ────────────────────────────────
        new Paragraph({ children: [new PageBreak()] }),
        heading2("Origin Country Breakdown (Top 8)"),
        countryTable,
      ],
    }],
  });

  // ── Write output ──────────────────────────────────────────────────────────
  const ts      = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const outPath = path.join(__dirname, "..", "reports", `airspace_report_${ts}.docx`);

  Packer.toBuffer(doc).then(buf => {
    fs.writeFileSync(outPath, buf);
    console.log(`[write_report] ✓ Written: ${outPath}`);
  });
}
