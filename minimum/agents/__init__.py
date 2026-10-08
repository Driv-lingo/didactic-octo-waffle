"""The AI faculty.

Seven roles, each a thin wrapper over a system prompt and an output schema.
What keeps them honest is not the prompts but the orchestrator, which decides
what each role is allowed to see (see ``minimum.orchestrator``).
"""

from .faculty import Advisor, Critic, Examiner, Grader, Pacer, Tutor, Verifier

__all__ = ["Advisor", "Critic", "Examiner", "Grader", "Pacer", "Tutor", "Verifier"]
