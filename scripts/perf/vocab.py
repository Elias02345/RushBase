"""Bilingual vocabulary and distributions for the synthetic perf catalog."""

Pair = tuple[str, str]

SUBJECTS: list[Pair] = [
    ("ein Fischer", "a fisherman"), ("eine Frau", "a woman"), ("ein Mann", "a man"),
    ("ein Kind", "a child"), ("zwei Radfahrer", "two cyclists"),
    ("eine Gruppe Wanderer", "a group of hikers"), ("ein Hund", "a dog"),
    ("ein Pferd", "a horse"), ("ein Traktor", "a tractor"), ("ein Lastwagen", "a truck"),
    ("eine Drohne", "a drone"), ("ein Segelboot", "a sailboat"), ("ein Zug", "a train"),
    ("ein Bäcker", "a baker"), ("eine Musikerin", "a musician"), ("ein Koch", "a chef"),
    ("Touristen", "tourists"), ("ein Schwarm Möwen", "a flock of seagulls"),
    ("ein Surfer", "a surfer"), ("eine Läuferin", "a runner"),
    ("ein Fahrradkurier", "a bike courier"), ("ein Skater", "a skater"),
    ("eine Tänzerin", "a dancer"), ("Arbeiter", "workers"), ("ein Landwirt", "a farmer"),
    ("ein Motorrad", "a motorcycle"), ("ein Hubschrauber", "a helicopter"),
    ("ein Paar", "a couple"), ("Schülerinnen", "schoolgirls"),
    ("ein Kameramann", "a camera operator"),
]  # fmt: skip
ACTIONS: list[Pair] = [
    ("geht", "walks"), ("läuft", "runs"), ("steht", "stands"), ("arbeitet", "works"),
    ("fährt vorbei", "drives past"), ("schaut in die Ferne", "looks into the distance"),
    ("lacht", "laughs"), ("winkt", "waves"), ("spielt", "plays"), ("wartet", "waits"),
    ("hebt etwas auf", "picks something up"), ("überquert die Straße", "crosses the street"),
    ("tanzt", "dances"), ("fotografiert", "takes photos"), ("repariert etwas", "repairs something"),
    ("bereitet Essen zu", "prepares food"), ("schwimmt", "swims"), ("klettert", "climbs"),
    ("ruht sich aus", "rests"), ("unterhält sich", "chats"),
]  # fmt: skip
LOCATIONS: list[Pair] = [
    ("am Hafen", "at the harbour"), ("auf dem Marktplatz", "on the market square"),
    ("im Wald", "in the forest"), ("am Strand", "on the beach"),
    ("in einer Küche", "in a kitchen"), ("auf einer Landstraße", "on a country road"),
    ("in der Altstadt", "in the old town"), ("auf einem Feld", "in a field"),
    ("im Büro", "in an office"), ("auf einer Baustelle", "on a construction site"),
    ("im Gebirge", "in the mountains"), ("an einem See", "by a lake"),
    ("in einer Werkstatt", "in a workshop"), ("am Bahnhof", "at the station"),
    ("auf dem Dach", "on a rooftop"), ("in einem Café", "in a cafe"),
    ("im Stadion", "in a stadium"), ("an der Küste", "on the coast"),
    ("im Park", "in the park"), ("in einer Fabrikhalle", "in a factory hall"),
    ("auf der Autobahn", "on the motorway"), ("im Weinberg", "in a vineyard"),
    ("im Studio", "in a studio"), ("am Flughafen", "at the airport"),
    ("im Hinterhof", "in a backyard"),
]  # fmt: skip
LIGHTS: list[Pair] = [
    ("bei Sonnenaufgang", "at sunrise"), ("im Gegenlicht", "backlit"),
    ("bei bedecktem Himmel", "under an overcast sky"),
    ("in der goldenen Stunde", "in the golden hour"), ("bei Nacht", "at night"),
    ("im Neonlicht", "under neon light"), ("bei hartem Mittagslicht", "in harsh midday light"),
    ("im Nebel", "in fog"), ("bei Regen", "in the rain"), ("im Schatten", "in the shade"),
]  # fmt: skip
MOODS: list[Pair] = [
    ("ruhig", "calm"), ("hektisch", "hectic"), ("feierlich", "festive"),
    ("melancholisch", "melancholic"), ("energiegeladen", "energetic"),
    ("nostalgisch", "nostalgic"), ("angespannt", "tense"), ("heiter", "cheerful"),
]  # fmt: skip
GENRES: list[Pair] = [
    ("Interview", "interview"), ("B-Roll", "b-roll"), ("Drohnenflug", "drone footage"),
    ("Zeitraffer", "timelapse"), ("Dokumentation", "documentary"), ("Werbung", "commercial"),
    ("Veranstaltung", "event"), ("Sport", "sport"), ("Reportage", "reportage"),
    ("Making-of", "making of"),
]  # fmt: skip
FILLERS_DE = [
    "Okay, wir machen das noch einmal von vorne.", "Kamera läuft, und bitte.",
    "Das Licht passt hier ziemlich gut.", "Kannst du bitte ein Stück nach links gehen?",
    "Wir haben noch zehn Minuten bis zum Sonnenuntergang.", "Der Ton ist in Ordnung.",
    "Super, genau so lassen wir es.", "Hier ist es leider ein bisschen windig.",
    "Gleich kommt der nächste Zug durch.", "Das war schon richtig gut.",
]  # fmt: skip
FILLERS_EN = [
    "Okay, let's do that once more from the top.", "Camera is rolling, and action.",
    "The light is actually quite good here.", "Could you move a little to the left please?",
    "We have ten minutes left before sunset.", "The sound is fine.",
    "Great, that is exactly how we keep it.", "It is a bit windy here unfortunately.",
    "The next train will come through in a moment.", "That was really good already.",
]  # fmt: skip
SHOT_SIZES = ["wide", "medium", "close-up", "extreme-close-up", "establishing", "full"]
ANGLES = ["eye-level", "low", "high", "aerial", "dutch"]
MOTIONS = ["static", "pan", "tilt", "handheld", "dolly", "zoom", "tracking"]
INT_EXT = ["interior", "exterior", "unknown"]
TIMES = ["dawn", "morning", "noon", "afternoon", "golden_hour", "dusk", "night"]
AUDIO_EVENTS = [
    "speech", "music", "applause", "gunshot", "explosion", "engine", "wind", "laughter",
    "siren", "door", "water", "birdsong",
]  # fmt: skip
CAMERAS: list[tuple[str, str, float]] = [
    ("Sony", "ILCE-7SM3", 18), ("Sony", "ILCE-7M4", 14), ("Canon", "EOS R5 C", 12),
    ("Canon", "EOS R6m2", 8), ("Panasonic", "DC-S5M2", 8),
    ("Blackmagic Design", "Pocket Cinema Camera 6K", 6), ("DJI", "Mavic 3", 9),
    ("DJI", "Osmo Action 4", 7), ("GoPro", "HERO12 Black", 6), ("Apple", "iPhone 15 Pro", 10),
    ("Nikon", "Z8", 4), ("Fujifilm", "X-H2S", 3),
]  # fmt: skip
LENSES = [
    "FE 24-70mm F2.8 GM II", "FE 70-200mm F2.8 GM", "FE 16-35mm F2.8 GM", "RF 24-70mm F2.8 L",
    "RF 15-35mm F2.8 L", "RF 70-200mm F2.8 L", "LUMIX S 20-60mm", "Sigma 18-35mm F1.8 Art",
    "Samyang 35mm T1.5", "Zeiss Supreme Prime 50mm", "Laowa 12mm T2.9", "Tamron 28-75mm F2.8",
    "Sigma 24mm F1.4 DG", "NIKKOR Z 24-70mm f/2.8", "XF 16-55mm F2.8", "built-in",
]  # fmt: skip
LOG_PROFILES = ["S-Log3", "C-Log3", "V-Log", "BRAW Film", "D-Log", "N-Log", "HLG", "F-Log2"]
VIDEO_CODECS = ["h264", "hevc", "prores", "braw", "dnxhd"]
VIDEO_RESOLUTIONS: list[tuple[int, int, float]] = [
    (3840, 2160, 35), (1920, 1080, 40), (4096, 2160, 8), (5760, 3240, 7), (7680, 4320, 2),
    (1080, 1920, 8),
]  # fmt: skip
PHOTO_RESOLUTIONS: list[tuple[int, int, float]] = [
    (6000, 4000, 40), (4000, 3000, 20), (8256, 5504, 15), (4000, 6000, 15), (7008, 4672, 10),
]  # fmt: skip
