"""Shared deterministic tuning for tests focused on mechanics other than aim."""


def force_clean_aims(config):
    rule = config["accuracy"]["aim_dispersion"]
    rule.update(clean_base=1, per_accuracy=0, difficulty_penalty=0,
                clean_min=1, clean_max=1, drift_share=0)
