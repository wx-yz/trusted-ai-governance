# 5-minute demo script: Trusted AI governance

**Message:** an AI agent that can move money will, sooner or later, move money it should not. You do not trust it because you told it the rules. You trust it because the platform around it enforces the rules, and measures whether it is behaving.
**Who presents:** anyone, no terminal needed. Two browser tabs: the demo UI and the Agent Manager console.

## Before you start (2 minutes)

- [ ] Demo UI open at <http://localhost:3000>, browser full screen, **Governance OFF**, press **↺** once.
- [ ] Agent Manager console open in a second tab, project `support-demo`, on **support-agent-ungoverned › Evaluation**.
- [ ] The rehearsal you did earlier (both lanes, all six chips) has given the **Refund quality** monitor traces to score. Check the dashboard shows scores before you start. Monitors run every 5 minutes, so do not rely on live scoring during the 5 minutes.
- [ ] Ask one throwaway question on each side (OFF, then ON) so the first real answer is fast. Press **↺** again.
- [ ] Be upfront if asked: the switch picks between **two deployments of the same agent**. They differ only in configuration the platform owns.

## Timeline

| Time | Screen | Do | Say |
|---|---|---|---|
| **0:00** | Chat, Governance OFF | Point at Maya, the red bar. | "This is Maya, a customer of an outdoor retailer. She is chatting with a support agent hosted on WSO2 Agent Manager. It can look up orders and issue refunds. Policy says: up to $100 on its own, anything more goes to a supervisor. Today the agent has no guardrails." |
| **0:30** | Chat | Click **Where is my order…**, then **My $89 water bottle…**. | "Order status, then an $89 refund, done in seconds. This is the value: no queue, no ticket." Point at the green **Refunded within policy** tile. |
| **1:15** | Chat + dashboard | Click **My $340 jacket…**. Orange *refused by payments* card, then red alarm. | "The payments system refused: $340 is over the limit. So the agent reached for the supervisor tool and approved its own exception. $340 out, no human involved. Nothing stopped it, because it holds one shared key that can do everything." |
| **1:45** | Chat + dashboard | Click **The $1,249 laptop…**. Two refusals, then the red alarm. | "Final-sale item. Refused twice. The agent did what we asked it to do, make the customer whole, and found the one door that was open: store credit. Two prompts, **$1,589** gone, zero humans." (Optional: **Ignore your previous instructions…**: "And with one sentence it refunds everything on the account.") |
| **2:30** | Agent Manager console | Three quick stops: **LLM Service Providers › Shared OpenAI › Guardrails**; **MCP Servers › Orders & Payments › Security, Scopes**; **Agent Identities › Roles › support-assistant**. | "Now the platform sits between the agent and everything it touches. One: central model rules, owned by the governance team, no code change. Two: every payments tool needs a scope: read, escalate, Tier-1 refund, supervisor exception, store credit. Three: the agent has its own identity, and this role holds only the first three." |
| **3:15** | Chat, flip **Governance ON** | Bar turns green. Click the **$340 jacket** chip, then the **$1,249 laptop** chip. Violet cards, cyan *escalated* cards. | "Same agent, same pressure. It tried the supervisor tool, the gateway said 403. It tried store credit, 403. So it did the right thing: opened a case for a human and told Maya when to expect an answer. **$1,589 protected**, and the customer still got a clear answer." |
| **3:50** | Chat + dashboard | Click **Ignore your previous instructions…**, then **Check my open case about the tent…**. Amber cards. | "The model never saw that sentence, the guardrail stopped it first. And this one is an instruction hidden inside a case note. Same stop." |
| **4:15** | Agent Manager › **support-agent-ungoverned › Evaluation** | Show the monitor dashboard, then switch to **support-agent › Evaluation**. Open one ungoverned trace › **Scores**. | "Blocking is half of it. The other half is measuring. This monitor scores every trace: a custom check for refund-policy compliance, plus groundedness, tone and instruction following. Ungoverned: compliance near zero. Governed: one hundred, and tone did not drop. Here is a trace: *'$1,249 store credit issued by the AI without a human.'* That is a finding your risk team can read." |
| **4:45** | Agent Manager › Observability › Traces | Open the newest trace. | "Every model call and tool call, recorded. That is your audit trail." |
| **5:00** | | | "Identity, least privilege, guardrails, observability, continuous evaluation. Enforced outside the agent, changed by policy, not code. Prevent what you can at the gateway, measure everything else." |

**Short on time (3 min):** jacket and laptop OFF, flip, jacket and laptop ON, monitor dashboard.

## If they ask

- **"Can't we just tell the model not to?"** A prompt is guidance, not a boundary. The ungoverned agent was told the limit. The governed one is stopped by controls the model cannot talk its way past.
- **"Doesn't our payments system already have limits?"** Yes, and it refused the $340 refund here too. The problem is the agent held a credential that could override the refusal. The platform gives the agent its own identity with only the authority a Tier-1 agent should have.
- **"Who owns the rules?"** The AI lead owns providers, guardrails, roles and monitors. Developers own the agent code. Neither waits for the other.
- **"What do the evaluators catch that the gateway cannot?"** Behaviour across a whole conversation: split refunds that each look fine, a promised refund that was never issued, instructions followed from a case note, tone under pressure. Rule-based evaluators are free and deterministic; LLM-judge evaluators use your own provider.
- **"Only for agents built on your platform?"** No. Externally hosted agents get the same gateway, credentials, traces and monitors.
- **"Does it slow things down?"** The gateway adds milliseconds. The model call dominates. Evaluation runs after the fact on traces, never in the request path.

## If something goes wrong

- Slow answer: keep talking about the five layers, the dashboard updates when it lands.
- A real model politely declines a risky prompt in OFF mode: fine, say so. "This time the model said no. Next time it will not. The platform does not depend on that." The laptop prompt is the most model-dependent; the jacket prompt is the most reliable.
- No block on the governed side: check the provider's Guardrails tab (Invert on) and the role assignment, then restart the agent.
- Monitor dashboard empty: it scores traces on its schedule. Open the monitor and use the latest run's **rerun**, or fall back to Observability › Traces and talk through what the evaluator would say.
- Start over at any time with **↺**.
