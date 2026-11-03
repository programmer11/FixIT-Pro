# FixIT-Pro documentation

FixIT-Pro matches home repair requests in the Santa Clara area to local technicians. It searches technician profiles in Pinecone and uses a local Qwen model to write a short recommendation. A seven-agent workflow then takes a tenant's request through owner approvals, quotes and scheduling to a confirmed visit.

For a quick start, see the [project README](../README.md). To see the app in action, watch the [search demo](../FixIT-Pro-demo.mp4) and the [workflow demo](../FixIT-Pro-workflow-demo.mp4).

| Guide | Read it when you want to… |
| --- | --- |
| [Architecture](architecture.md) | Understand how the pieces fit together and why |
| [Repair request workflow](workflow.md) | Understand the seven agents, approvals, ranking and internet search |
| [Code reference](code-reference.md) | Find a function, state field or file, and make common changes |
| [Setup and configuration](setup.md) | Install, configure and run the app |
| [User guide](user-guide.md) | Search for technicians, or submit and follow a repair request as a tenant |
| [Admin guide](admin-guide.md) | Sign in as admin, upload profiles, and approve requests in the owner console |
| [Profile data format](data-format.md) | Prepare or edit a profiles JSON file |
| [API reference](api.md) | Call the HTTP endpoints directly |
| [Troubleshooting](troubleshooting.md) | Fix common problems |
