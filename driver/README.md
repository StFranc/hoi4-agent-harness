# driver — local LLM game driver

`driver.py` is our local AI-player driver for the `llm_bridge` mod: it polls the
mod's REST bridge each game hour, sends the game state to a local planner
(Qwen3-8B via llama-server, default `:8081`), and writes back orders/
directives. `turn_cmd.txt` is the hand-off note format it consumes.

It is standalone (stdlib + `requests`) and intentionally kept out of the
upstream Python package — it belongs to our local setup
(`ai-infra\` supplies the inference servers).

Run it with any Python 3.11+ that has `requests` installed:

```powershell
python driver\driver.py --help
```

Mirrored from `Projects\game-harnesses\hoi4\` — edit there, copy here when it
stabilizes (the game-harnesses copy is the live one).
