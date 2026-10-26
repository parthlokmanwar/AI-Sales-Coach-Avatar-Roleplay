"""Per-avatar Hindi (ElevenLabs) voice overrides for RolePlay.

In a Hindi session the Tavus replica's own (English) voice is replaced by an
ElevenLabs voice. By default RolePlay picks just TWO voices by gender
(ELEVENLABS_VOICE_ID_MALE / _FEMALE in .env), so every male avatar sounds the
same and every female avatar sounds the same.

This map gives EACH avatar its own Hindi voice, keyed by Tavus replica_id (the
face). features/roleplay.py prefers the voice mapped here; if a replica is
absent OR its value is blank, it falls back to the gender-matched .env voice —
so Hindi always works even before every slot is filled in.

Why keyed by replica_id (not preset id): the launch payload always carries the
avatar's replica_id, for BOTH the preset gallery and the custom form. So one map
covers every path, and the same face always gets the same voice. The 8 gallery
faces appear as 16 cards (the New and Existing rows reuse the same replica), so
the same person sounds identical in both rows — exactly as intended.

TO FILL IN: paste an ElevenLabs voice_id from your account into each value. Pick
a Hindi / Indian-accent voice that fits the avatar's gender and rough age. The
two voices already in .env you can reuse as examples:
    Viraj   (male)   iWNf11sz1GrUE4ppxTOL
    Shanaya (female) zMndFmtlJvAIQjxXWZTU
"""

# Tavus replica_id -> ElevenLabs voice_id. Empty string ("") = fall back to the
# gender-matched default voice from .env (ELEVENLABS_VOICE_ID_MALE / _FEMALE).
HINDI_VOICE_BY_REPLICA = {
    # ── Male avatars ──
    "r621a6013477": "",  # Dr. Rajesh Mehta  — Male, 35-50  (Raj - Doctor)
    "r987f6e6f73c": "",  # Suresh Iyer       — Male, 50+    (Nathan - Bookshelf)
    "re3fd4adeafd": "",  # Amit Shah         — Male, 35-50  (Victor - Office)
    "r72f7f7f7c8b": "",  # Pradeep Kumar     — Male, 50+    (Daniel - Office)
    "rdba02690e60": "",  # Vikas Malhotra    — Male, 35-50  (Phoenix-4)
    # ── Female avatars ──
    "rd3ba0f30551": "",  # Dr. Ananya Reddy  — Female, 35-50 (Olivia - Doctor)
    "r12d3eb75ec2": "",  # Sunita Deshpande  — Female, 35-50 (Helen - Casual)
    "r55e6793f10f": "",  # Kavita Reddy      — Female, 25-35 (Mary - Business)
    "r3f427f43c9d": "",  # Meena Sharma      — Female, 35-50 (Gloria - Warm)
    "r84698ecbd7a": "",  # Anjali Verma      — Female, 35-50 (Housewife)
}


def voice_for_replica(replica_id: str) -> str:
    """Return the mapped Hindi voice_id for this avatar, or "" if none/blank.

    Callers treat "" as "use the gender-matched default", so an unmapped or
    not-yet-filled avatar degrades safely instead of breaking the Hindi launch.
    """
    return (HINDI_VOICE_BY_REPLICA.get((replica_id or "").strip()) or "").strip()
