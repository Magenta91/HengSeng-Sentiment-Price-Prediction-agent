from app.services.grounding import apply_grounding

TEXT = "Long 0700 from 480. Resistance 520, support at 470. Great company."


def base(**kw):
    d = {"support_levels": [], "resistance_levels": [], "target_price": None, "position": None, "evidence_text": None}
    d.update(kw)
    return d


def test_keeps_stated_levels_and_drops_invented_ones():
    out, notes = apply_grounding(base(support_levels=[470, 455], resistance_levels=[520], target_price=550), TEXT)
    assert out["support_levels"] == [470] and out["resistance_levels"] == [520] and out["target_price"] is None
    assert len(notes) == 2


def test_position_requires_verbatim_evidence():
    ok, _ = apply_grounding(base(position={"type": "long", "entry_price": 480, "quantity": 1000,
                                           "evidence_text": "Long 0700 from 480"}), TEXT)
    assert ok["position"]["type"] == "long" and ok["position"]["entry_price"] == 480 and ok["position"]["quantity"] is None
    bad, notes = apply_grounding(base(position={"type": "long", "evidence_text": "I bought shares"}), TEXT)
    assert bad["position"] is None and notes
    none_ev, _ = apply_grounding(base(position={"type": "short"}), TEXT)
    assert none_ev["position"] is None


def test_evidence_must_be_verbatim_excerpt():
    out, _ = apply_grounding(base(evidence_text="great   company"), TEXT)
    assert out["evidence_text"] == "great   company"
    out, notes = apply_grounding(base(evidence_text="to the moon"), TEXT)
    assert out["evidence_text"] is None and notes
