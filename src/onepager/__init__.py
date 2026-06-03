"""onepager-agent: fully-sourced, confidence-tagged company one-pager generator.

The whole system is built on one rule (the *Grounding Contract*): no claim is
emitted unless (a) it is backed by a retrieved source span and (b) an independent
verifier confirms that span actually supports it. The LLM is never the source of a
fact; it only orchestrates retrieval, extraction, and synthesis-with-attribution.
"""

__version__ = "0.1.0"
