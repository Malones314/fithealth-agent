import unittest
from fithealth_agent.plan_workflow import PlanWorkflowState, state_after_generation, state_for_context

class PlanWorkflowTest(unittest.TestCase):
    def test_context_states(self):
        self.assertEqual(state_for_context({"clarification_required": True}), PlanWorkflowState.NEEDS_CLARIFICATION)
        self.assertEqual(state_for_context({"blocking_reasons": ["pain"]}), PlanWorkflowState.CONSTRAINT_CONFLICT)
        self.assertEqual(state_for_context({}), PlanWorkflowState.READY_TO_GENERATE)

    def test_generation_states(self):
        self.assertEqual(state_after_generation(artifact=None, validation_failed=False), PlanWorkflowState.COMPLETED)
        self.assertEqual(state_after_generation(artifact={}, validation_failed=True), PlanWorkflowState.VALIDATION_FAILED)
        self.assertEqual(state_after_generation(artifact={"type": "training_plan"}, validation_failed=False), PlanWorkflowState.AWAITING_SAVE)

if __name__ == "__main__":
    unittest.main()
