import type {
  AiInfo,
  AssetDetail,
  AssetSummary,
  AudioEvent,
  GoldenField,
  Proposal,
  SearchHit,
  Shot,
  ShotAi,
  Taxonomy,
  TaxonomyField,
  TaxonomyValue,
  TranscriptSegment,
} from "../contracts";
import { createRandom } from "./data";

/** Mock AI layer: taxonomy, descriptions, tags, transcripts and review state. */

const SHOT_SIZES = ["wide", "medium", "close_up", "extreme_wide", "detail"] as const;
const ANGLES = ["eye_level", "low", "high", "top_down"] as const;
const SETTINGS = ["interior", "exterior"] as const;
const TIMES = ["day", "golden_hour", "dusk", "night"] as const;

const SCENES_DE = [
  "Fischerboote liegen im Hafen, Möwen kreisen darüber.",
  "Ein Mann geht über den Markt und prüft Obst.",
  "Eine Drohne fliegt über einen Bergkamm im Gegenlicht.",
  "Nahaufnahme von Händen in einer Werkstatt.",
  "Straße bei Nacht mit Spiegelungen im nassen Asphalt.",
  "Interview in einem hellen Studio, Person im Halbprofil.",
];
const SCENES_EN = [
  "Fishing boats lie in the harbour with gulls circling above.",
  "A man walks across the market inspecting fruit.",
  "A drone flies over a mountain ridge against the light.",
  "Close-up of hands in a workshop.",
  "Street at night with reflections in wet asphalt.",
  "Interview in a bright studio, person in half profile.",
];
const TRANSCRIPT = [
  "Willkommen am Hafen, hier beginnt unsere Reise.",
  "Der Leuchtturm ist über hundert Jahre alt.",
  "Man hört die Boote schon am frühen Morgen.",
  "Wir fahren weiter zum Markt in der Altstadt.",
  "Dort gibt es frischen Fisch und Gemüse aus der Region.",
  "Das war der erste Tag, morgen geht es in die Berge.",
];

function value(
  id: string,
  fieldId: string,
  name: string,
  de: string,
  en: string,
  status: TaxonomyValue["status"],
  synonyms: string[] = [],
  proposals = 0,
): TaxonomyValue {
  return {
    id,
    field_id: fieldId,
    value: name,
    label_de: de,
    label_en: en,
    synonyms,
    parent_id: null,
    status,
    description: "",
    proposal_count: proposals,
    asset_count: status === "approved" ? 12 : 0,
  };
}

export function buildTaxonomy(): Taxonomy {
  const fields: TaxonomyField[] = [
    {
      id: "f0000000-0000-4000-8000-000000000001",
      key: "location",
      label_de: "Ort",
      label_en: "Location",
      kind: "single",
      description: "",
      sort: 1,
      values: [
        value(
          "v0000000-0000-4000-8000-000000000011",
          "f0000000-0000-4000-8000-000000000001",
          "harbour",
          "Hafen",
          "Harbour",
          "approved",
          ["port"],
        ),
        value(
          "v0000000-0000-4000-8000-000000000012",
          "f0000000-0000-4000-8000-000000000001",
          "market",
          "Markt",
          "Market",
          "approved",
        ),
        value(
          "v0000000-0000-4000-8000-000000000013",
          "f0000000-0000-4000-8000-000000000001",
          "mountain",
          "Berg",
          "Mountain",
          "approved",
        ),
        value(
          "v0000000-0000-4000-8000-000000000014",
          "f0000000-0000-4000-8000-000000000001",
          "pier",
          "Pier",
          "Pier",
          "proposed",
          [],
          7,
        ),
      ],
    },
    {
      id: "f0000000-0000-4000-8000-000000000002",
      key: "mood",
      label_de: "Stimmung",
      label_en: "Mood",
      kind: "multi",
      description: "",
      sort: 2,
      values: [
        value(
          "v0000000-0000-4000-8000-000000000021",
          "f0000000-0000-4000-8000-000000000002",
          "calm",
          "Ruhig",
          "Calm",
          "approved",
        ),
        value(
          "v0000000-0000-4000-8000-000000000022",
          "f0000000-0000-4000-8000-000000000002",
          "dramatic",
          "Dramatisch",
          "Dramatic",
          "approved",
        ),
        value(
          "v0000000-0000-4000-8000-000000000023",
          "f0000000-0000-4000-8000-000000000002",
          "melancholic",
          "Melancholisch",
          "Melancholic",
          "proposed",
          [],
          3,
        ),
      ],
    },
  ];
  return { version: 3, fields };
}

export function buildProposals(taxonomy: Taxonomy): Proposal[] {
  return taxonomy.fields.flatMap((field) =>
    field.values
      .filter((v) => v.status === "proposed")
      .map((v) => ({
        ...v,
        field_key: field.key,
        field_label_de: field.label_de,
        field_label_en: field.label_en,
        examples: [],
      })),
  );
}

export function buildGoldenFields(taxonomy: Taxonomy): GoldenField[] {
  const core: [string, string, string, string[]][] = [
    [
      "shot_size",
      "Einstellungsgröße",
      "Shot size",
      [
        "extreme_wide",
        "wide",
        "full",
        "medium",
        "medium_close",
        "close_up",
        "extreme_close_up",
        "detail",
      ],
    ],
    [
      "camera_angle",
      "Kamerawinkel",
      "Camera angle",
      ["eye_level", "low", "high", "birds_eye", "top_down", "dutch", "pov"],
    ],
    [
      "interior_exterior",
      "Innen/Außen",
      "Interior/exterior",
      ["interior", "exterior", "mixed", "unknown"],
    ],
    [
      "time_of_day",
      "Tageszeit",
      "Time of day",
      ["dawn", "day", "golden_hour", "dusk", "night", "unknown"],
    ],
    ["people_count", "Personenzahl", "People count", ["0", "1", "2", "3", "4", "5", "6", "7+"]],
    ["person_identifiable", "Person erkennbar", "Person identifiable", ["yes", "no", "unknown"]],
  ];
  return [
    ...core.map(([key, de, en, values]) => ({
      key,
      label_de: de,
      label_en: en,
      kind: "core",
      values,
      multi: false,
    })),
    ...taxonomy.fields.map((f) => ({
      key: f.key,
      label_de: f.label_de,
      label_en: f.label_en,
      kind: "taxonomy",
      values: f.values.filter((v) => v.status === "approved").map((v) => v.value),
      multi: f.kind === "multi",
    })),
  ];
}

export function reviewReasons(index: number): string[] {
  return index % 2 === 0
    ? ["shot0:low_confidence:shot_size", "vlm_siglip_conflict"]
    : ["low_confidence:time_of_day"];
}

export function buildShotAi(summary: AssetSummary, shot: Shot): ShotAi {
  const random = createRandom(summary.id.length * 31 + shot.idx * 17 + shot.start_ms);
  const n = random.int(0, SCENES_DE.length - 1);
  return {
    description_short_de: SCENES_DE[n] ?? null,
    description_short_en: SCENES_EN[n] ?? null,
    description_long_de: `${SCENES_DE[n] ?? ""} Die Kamera bleibt ruhig, das Licht ist natürlich.`,
    description_long_en: `${SCENES_EN[n] ?? ""} The camera stays steady and the light is natural.`,
    shot_size: random.pick(SHOT_SIZES),
    camera_angle: random.pick(ANGLES),
    interior_exterior: random.pick(SETTINGS),
    time_of_day: random.pick(TIMES),
    people_count: random.int(0, 3),
    person_identifiable: random.pick(["yes", "no", "unknown"] as const),
    human_fields: [],
  };
}

export function buildAi(
  summary: AssetSummary,
  shots: Shot[],
  taxonomy: Taxonomy,
  index: number,
): AiInfo {
  const random = createRandom(index * 977 + 13);
  const analysed = summary.status === "filed" || summary.status === "review";
  const hasText = analysed && summary.media_type !== "photo";
  const location = taxonomy.fields[0];
  const mood = taxonomy.fields[1];
  const pickValue = (field: TaxonomyField | undefined) => {
    const approved = (field?.values ?? []).filter((v) => v.status === "approved");
    return approved[random.int(0, Math.max(0, approved.length - 1))];
  };
  const tags =
    analysed && location && mood
      ? [location, mood].flatMap((field, i) => {
          const v = pickValue(field);
          if (!v) return [];
          return [
            {
              id: `t${index}-${i}`,
              field_key: field.key,
              field_label_de: field.label_de,
              field_label_en: field.label_en,
              value: v.value,
              label_de: v.label_de,
              label_en: v.label_en,
              source: i === 0 ? "ai" : random.pick(["ai", "human"] as const),
              confidence:
                i === 0 || random.next() < 0.5 ? Math.round(random.next() * 40 + 55) / 100 : 1,
              shot_idx: null,
            },
          ];
        })
      : [];
  return {
    summary_short_de: analysed ? (SCENES_DE[index % SCENES_DE.length] ?? null) : null,
    summary_short_en: analysed ? (SCENES_EN[index % SCENES_EN.length] ?? null) : null,
    summary_long_de: analysed
      ? `${SCENES_DE[index % SCENES_DE.length] ?? ""} Das Material eignet sich als B-Roll.`
      : null,
    summary_long_en: analysed
      ? `${SCENES_EN[index % SCENES_EN.length] ?? ""} The footage works as B-roll.`
      : null,
    shot_size: summary.media_type === "photo" && analysed ? random.pick(SHOT_SIZES) : null,
    camera_angle: summary.media_type === "photo" && analysed ? random.pick(ANGLES) : null,
    interior_exterior: analysed ? random.pick(SETTINGS) : null,
    time_of_day: analysed ? random.pick(TIMES) : null,
    people_count: analysed ? random.int(0, 3) : null,
    person_identifiable: "unknown",
    consent: "unknown",
    consent_ref: null,
    rating: null,
    locked: summary.locked,
    review_state: summary.review_state,
    review_reasons: summary.review_state === "pending" ? reviewReasons(index) : [],
    human_fields: [],
    audio: hasText ? { has_audio: true, language: "de" } : null,
    models: analysed ? { vision: { model_id: "qwen3.5-4b-q8_0" } } : {},
    tags,
    transcript_segments: hasText && index % 2 === 0 ? TRANSCRIPT.length : 0,
    audio_events: hasText && index % 2 === 0 ? 2 : 0,
  };
}

export function buildTranscript(durationMs: number): {
  segments: TranscriptSegment[];
  events: AudioEvent[];
} {
  const step = Math.max(2000, Math.floor(durationMs / TRANSCRIPT.length));
  return {
    segments: TRANSCRIPT.map((text, idx) => ({
      idx,
      start_ms: idx * step,
      end_ms: Math.min(durationMs, idx * step + step - 300),
      text,
      language: "de",
      speaker: null,
      words: [],
    })),
    events: [
      {
        tag: "applause",
        start_ms: Math.floor(durationMs * 0.7),
        end_ms: Math.floor(durationMs * 0.75),
        confidence: 0.8,
      },
      {
        tag: "wind",
        start_ms: Math.floor(durationMs * 0.2),
        end_ms: Math.floor(durationMs * 0.3),
        confidence: 0.6,
      },
    ],
  };
}

export function hitFor(asset: AssetSummary, shot: Shot | null): SearchHit {
  const frame = shot?.keyframes.find((k) => k.position === "middle") ?? shot?.keyframes[0];
  return {
    asset,
    shot: shot
      ? {
          id: shot.id,
          idx: shot.idx,
          start_ms: shot.start_ms,
          end_ms: shot.end_ms,
          keyframe_url: frame?.url ?? null,
          description_de: shot.ai?.description_short_de ?? null,
          description_en: shot.ai?.description_short_en ?? null,
        }
      : null,
    score: 0.03,
    ranks: { fulltext: 1, text: 2 },
  };
}

/** Complete a mock detail with the AI section and per-shot descriptions. */
export function withAi(
  detail: Omit<AssetDetail, "ai">,
  taxonomy: Taxonomy,
  index: number,
): AssetDetail {
  const analysed = detail.status === "filed" || detail.status === "review";
  const shots = detail.shots.map((shot) => ({
    ...shot,
    ai: analysed ? buildShotAi(detail, shot) : null,
  }));
  return { ...detail, shots, ai: buildAi(detail, shots, taxonomy, index) };
}
