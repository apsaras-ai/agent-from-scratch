"""Chapter 29: bounded inference-time search, independent verifier, UCT.

Zero external systems: no LLM API, arbitrary code execution, filesystem
writes, network tools or privileged actions in the inference_search module.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import random
import unittest

from mini_agent.inference_search import (
    Candidate, SearchBudget, SearchError, SynthesisTask, ToyProposer,
    _SearchSession, _transition, _uct, compare_strategies,
    derive_search_seed, run_search,
)


class AlwaysGreedy:
    def choose(self, task, state, options, rng):
        legal = [
            op for op in options
            if _transition(state,op,task.max_abs_value) is not None
        ]
        return min(legal, key=lambda op: (
            abs(_transition(state,op,task.max_abs_value)-task.target),
            options.index(op),
        )) if legal else ""


class IllegalTool:
    def choose(self, task, state, options, rng):
        return "send_email"


class ImpossiblePolicy:
    def choose(self, task, state, options, rng):
        return "add1"


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.easy = SynthesisTask(
            "easy", initial=1, target=7, max_steps=4,
            allowed_ops=("add1","add3","mul2","sub1")
        )
        self.budget = SearchBudget(
            proposals=100, transitions=100,
            verifications=20, candidates=20,
        )

    def run_easy(self,method):
        return run_search(self.easy,method,budget=self.budget,
                          proposer=AlwaysGreedy(),seed=11)

    def test_greedy_best_of_n_recovers_verified_program(self):
        result=self.run_easy("best_of_n")
        self.assertTrue(result.success)
        self.assertEqual(result.program,("add3","add3"))
        self.assertEqual(result.final_state,7)
        self.assertEqual(result.verifications,1)
        self.assertGreater(result.synthetic_cost,0)

    def test_self_consistency_greedy_success_and_one_verification(self):
        result=self.run_easy("self_consistency")
        self.assertTrue(result.success)
        self.assertEqual(result.program,("add3","add3"))
        self.assertEqual(result.verifications,1)
        self.assertEqual(result.candidates,self.budget.candidates)

    def test_beam_uses_trusted_verifier_not_only_heuristic(self):
        result=self.run_easy("beam")
        self.assertTrue(result.success)
        self.assertEqual(result.program,("add3","add3"))
        self.assertEqual(result.verifications,1)

    def test_mcts_expansion_rollout_and_backup(self):
        result=self.run_easy("mcts")
        self.assertTrue(result.success)
        self.assertEqual(result.final_state,7)
        self.assertGreaterEqual(result.proposals,2)
        self.assertEqual(result.verifications,1)

    def test_verify_replays_from_initial_not_forged_candidate_state(self):
        ctx=_SearchSession(self.easy,self.budget,AlwaysGreedy(),seed=1)
        self.assertFalse(ctx.verify(Candidate(("add1",),7)))
        self.assertIsNone(ctx.last_validated)
        self.assertEqual(ctx.meter.verifications,1)

    def test_invalid_action_is_denied_and_not_executed(self):
        budget=replace(self.budget,candidates=5,proposals=6,transitions=6)
        result=run_search(self.easy,"best_of_n",budget=budget,proposer=IllegalTool())
        self.assertFalse(result.success)
        self.assertEqual(result.proposals,5)
        self.assertEqual(result.transitions,0)
        self.assertEqual(result.rejected_actions,5)
        self.assertIsNone(result.program)

    def test_illegal_action_never_produces_verified_program(self):
        for method in ("self_consistency","beam","mcts"):
            with self.subTest(method=method):
                r=run_search(self.easy,method,budget=replace(
                    self.budget,candidates=4,proposals=6),
                    proposer=IllegalTool())
                self.assertFalse(r.success)
                self.assertIsNone(r.program)
                self.assertEqual(r.transitions,0)

    def test_unknown_method_rejected(self):
        with self.assertRaises(SearchError):
            run_search(self.easy,"unsafe-shell")

    def test_task_bounded_int_types_and_ops(self):
        for modified in (
            {"task_id":"../escape"},
            {"initial":True},
            {"target":129},
            {"max_steps":0},
            {"max_steps":20},
            {"allowed_ops":("add1","add1")},
            {"allowed_ops":("shell",)},
            {"max_abs_value":0},
        ):
            with self.subTest(modified=modified),self.assertRaises(SearchError):
                replace(self.easy,**modified)

    def test_budgets_fail_closed_on_nan_and_bool(self):
        for modified in (
            {"proposals":True},
            {"transitions":0},
            {"candidates":-1},
            {"proposal_cost":Decimal("NaN")},
            {"verification_cost":Decimal("-0.2")},
            {"transition_cost":Decimal("Infinity")},
        ):
            with self.subTest(modified=modified),self.assertRaises(SearchError):
                replace(self.budget,**modified)

    def test_cost_accounting_includes_unsafe_and_failed_requests(self):
        budget=SearchBudget(proposals=3,transitions=4,verifications=3,
                            candidates=3,proposal_cost="2",transition_cost="3",
                            verification_cost="5")
        r=run_search(self.easy,"best_of_n",budget=budget,
                     proposer=IllegalTool())
        self.assertEqual(r.synthetic_cost,Decimal("6"))
        self.assertEqual(r.verifications,0)

    def test_hard_caps_never_exceeded_by_any_search_strategy(self):
        budget=SearchBudget(proposals=4,transitions=3,verifications=2,candidates=2)
        for method in ("best_of_n","self_consistency","beam","mcts"):
            with self.subTest(method=method):
                r=run_search(self.easy,method,budget=budget,seed=42)
                self.assertLessEqual(r.proposals,budget.proposals)
                self.assertLessEqual(r.transitions,budget.transitions)
                self.assertLessEqual(r.verifications,budget.verifications)
                self.assertLessEqual(r.candidates,budget.candidates)

    def test_tight_transition_budget_cannot_magically_succeed(self):
        budget=replace(self.budget,transitions=1)
        for method in ("best_of_n","self_consistency","beam","mcts"):
            with self.subTest(method=method):
                result=run_search(self.easy,method,budget=budget,
                                  proposer=AlwaysGreedy())
                self.assertFalse(result.success)

    def test_invalid_verifier_claimed_path_and_negative_bound(self):
        ctx=_SearchSession(self.easy,self.budget,AlwaysGreedy(),0)
        self.assertFalse(ctx.verify(Candidate(("send_email",),7)))
        self.assertFalse(ctx.verify(Candidate(("sub1",),7)))
        self.assertIsNone(_transition(0,"sub1",128))
        self.assertIsNone(_transition(100,"mul2",128))

    def test_self_consistency_majority_can_be_wrong(self):
        task=SynthesisTask("vote-wrong",initial=1,target=8,max_steps=1,
                           allowed_ops=("add1","mul2"))
        r=run_search(task,"self_consistency",budget=replace(self.budget,candidates=7),
                     proposer=ImpossiblePolicy())
        self.assertFalse(r.success)
        self.assertEqual(r.verifications,1)
        self.assertIsNone(r.program)

    def test_best_of_n_verification_calls_cost_more_than_vote(self):
        task=SynthesisTask("vote-wrong",initial=1,target=8,max_steps=1,
                           allowed_ops=("add1","mul2"))
        budget=replace(self.budget,candidates=4)
        a=run_search(task,"best_of_n",budget=budget,proposer=ImpossiblePolicy())
        b=run_search(task,"self_consistency",budget=budget,proposer=ImpossiblePolicy())
        self.assertEqual(a.verifications,4)
        self.assertEqual(b.verifications,1)
        self.assertGreater(a.synthetic_cost,b.synthetic_cost)

    def test_start_at_goal_requires_host_verification(self):
        task=replace(self.easy,target=1)
        result=run_search(task,"best_of_n",budget=self.budget)
        self.assertTrue(result.success)
        self.assertEqual(result.program,())
        self.assertEqual(result.verifications,1)

    def test_methods_deterministic_at_given_seed(self):
        task=SynthesisTask("x",initial=2,target=19,max_steps=5)
        for method in ("best_of_n","self_consistency","beam","mcts"):
            with self.subTest(method=method):
                a=run_search(task,method,budget=self.budget,seed=45)
                b=run_search(task,method,budget=self.budget,seed=45)
                self.assertEqual(a,b)

    def test_cannot_claim_success_for_unreachable_goal(self):
        task=SynthesisTask("impossible",initial=1,target=31,
                           max_steps=1,allowed_ops=("add1",))
        for method in ("best_of_n","self_consistency","beam","mcts"):
            with self.subTest(method=method):
                r=run_search(task,method,budget=self.budget,seed=7)
                self.assertFalse(r.success)
                self.assertIsNone(r.program)

    def test_seed_derivation_varies_by_case_trial(self):
        self.assertEqual(derive_search_seed(42,"case-a",1),
                         derive_search_seed(42,"case-a",1))
        self.assertNotEqual(derive_search_seed(42,"case-a",1),
                            derive_search_seed(42,"case-a",2))
        self.assertNotEqual(derive_search_seed(42,"case-a",1),
                            derive_search_seed(42,"case-b",1))
        for bad in (-1,True,2**64):
            with self.assertRaises(SearchError):
                derive_search_seed(bad,"case-a",1)

    def test_uct_unvisited_is_prioritized_and_visit_count_matters(self):
        from mini_agent.inference_search import _MCTSNode
        a=_MCTSNode(state=2,path=("add1",))
        self.assertEqual(_uct(a,1,1.4),float("inf"))
        a.visits=1
        a.value=0.5
        self.assertLess(_uct(a,1,1.4),_uct(a,100,1.4))

    def test_invalid_beam_and_mcts_parameters(self):
        for arg in (
            {"width":0},
            {"branch":False},
            {"exploration":float("nan")},
            {"exploration":-1},
        ):
            with self.subTest(arg=arg),self.assertRaises(SearchError):
                run_search(self.easy,"mcts",**arg)

    def test_untrusted_policy_exception_cannot_escape_or_execute(self):
        class Explosive:
            def choose(self,*args):
                raise RuntimeError("secret-in-exception")
        r=run_search(self.easy,"beam",budget=replace(self.budget,proposals=6),
                     proposer=Explosive())
        self.assertFalse(r.success)
        self.assertGreater(r.rejected_actions,0)
        self.assertNotIn("secret-in-exception",repr(r))

    def test_compare_methods_with_shared_ceiling_and_costs(self):
        tasks=(self.easy,SynthesisTask("other",2,17,5),
               SynthesisTask("third",1,19,5))
        rows=compare_strategies(tasks,budget=self.budget,seed=44)
        self.assertEqual(len(rows),4)
        self.assertEqual(set(x.method for x in rows),
                         {"best_of_n","self_consistency","beam","mcts"})
        for s in rows:
            self.assertEqual(s.tasks,3)
            self.assertGreaterEqual(s.success_rate,0)
            self.assertLessEqual(s.success_rate,1)
            self.assertLessEqual(s.total_proposals,3*self.budget.proposals)
            self.assertLessEqual(s.total_verifications,3*self.budget.verifications)
        self.assertEqual(rows,compare_strategies(tasks,budget=self.budget,seed=44))

    def test_duplicate_cases_and_methods_rejected(self):
        with self.assertRaises(SearchError):
            compare_strategies((self.easy,self.easy))
        with self.assertRaises(SearchError):
            compare_strategies((self.easy,),methods=("beam","beam"))
        with self.assertRaises(SearchError):
            compare_strategies(())

    def test_bad_policy_or_probability_rejected(self):
        with self.assertRaises(SearchError):
            ToyProposer(float("inf"))
        with self.assertRaises(SearchError):
            run_search(self.easy,"beam",proposer=object())


if __name__ == "__main__":
    unittest.main()
