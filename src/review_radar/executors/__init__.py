"""Executors: the only code that writes outside this database.

Imported by `service.ProposalService.approve` and the export route, never by `agent/`
(a test asserts the agent package does not import this one). The agent proposes; a human
approves; an executor acts on the *stored* proposal, so nothing the model says after the
fact can change what is executed.
"""
