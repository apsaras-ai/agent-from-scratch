"""Chapter 30: honest verifier calibration and synthetic feedback learning.

All tasks are public toy arithmetic programs. No external LLM weights,
untrusted executable programs, browsers, payments or network connections.
"""
from pathlib import Path

from mini_agent.feedback_learning import (
    TrustedArithmeticVerifier, audit_reward_model, biased_length_reward,
    collect_train_feedback, compare_proxy_and_host,
    evaluate_heldout_policy, fit_tabular_feedback_policy,
    load_feedback_suite,
)
from mini_agent.inference_search import SynthesisTask


def main() -> None:
    verifier = TrustedArithmeticVerifier()
    challenge = SynthesisTask("reward-hacking", 1, 4, 4)
    correct = ("add3",)
    wrong_but_long = ("add1", "add1", "add1", "add1")

    print("A. Outcome verifier versus proxy reward")
    good_evidence = verifier.verify(challenge, correct)
    bad_evidence = verifier.verify(challenge, wrong_but_long)
    print("  Correct path:", good_evidence.process_ok,
          good_evidence.outcome_ok)
    print("  Long wrong path:", bad_evidence.process_ok,
          bad_evidence.outcome_ok)
    print("  Proxy length scores:",
          biased_length_reward(challenge, correct),
          biased_length_reward(challenge, wrong_but_long))
    selection = compare_proxy_and_host(
        challenge, (correct, wrong_but_long),
    )
    print("  Proxy chose:", selection.proxy_selected,
          "actual verified:", selection.proxy_verified)
    print("  Trusted selection:", selection.verified_selected,
          "verified:", selection.verified_success)

    print("B. Reward-model reliability audit")
    audit = audit_reward_model([
        (challenge, correct, 0.25),
        (challenge, wrong_but_long, 1.0),
    ])
    print("  TP, FP, TN, FN:", audit.tp, audit.fp, audit.tn, audit.fn)
    print("  Brier / ECE:", round(audit.brier, 4),
          round(audit.expected_calibration_error, 4))

    print("C. Train-only trusted feedback, fresh heldout evaluation")
    suite = load_feedback_suite(
        Path(__file__).resolve().parents[1] /
        "evals" / "ch30_feedback_suite.json"
    )
    train = suite.for_split("train")
    heldout = suite.for_split("heldout")
    records = collect_train_feedback(suite)
    policy = fit_tabular_feedback_policy(suite, records)
    baseline = evaluate_heldout_policy(suite)
    learned = evaluate_heldout_policy(suite, policy=policy)
    print("  Dataset:", suite.suite_id, suite.version,
          "train tasks", len(train), "heldout tasks", len(heldout))
    print("  Trusted training labels:", len(records))
    print("  Baseline heldout:", f"{baseline.verified}/{baseline.trials}")
    print("  Tabular learned heldout:", f"{learned.verified}/{learned.trials}")
    print("  Suite digest:", suite.digest()[:16])
    assert selection.proxy_verified is False
    assert selection.verified_success is True
    assert (baseline.verified, learned.verified) == (3, 6)
    print("Only a synthetic tabular policy learned; no LLM or production deployment.")


if __name__ == "__main__":
    main()
