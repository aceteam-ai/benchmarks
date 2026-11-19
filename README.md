# AceTeam Benchmarks

Auto-scored benchmarks for local models served by [Ollama](https://ollama.com), on one GPU or many. Code scores every task: unit tests, exact answers, or SQL run against a known database. No judge model.

We use it to pick which model our agents run on hardware we own.

## What it tests

**Short suite (10 single-turn tasks)**

| Task | Checked by |
| - | - |
| JSON extraction from an email | exact field match |
| `is_palindrome`, `merge_intervals`, a bug fix | unit tests |
| Two tool calls (pick the right tool, fill the arguments) | tool name and argument values |
| A word problem | the final number |
| A SQL aggregate | the query is run in SQLite and the rows compared |
| A Canadian postal code regex | 3 strings that must match, 5 that must not |
| A formatting instruction | line count, bullet prefix, word count |

**Long suite (6 tasks)**

| Task | Checked by |
| - | - |
| Recall at about 16k, 32k and 64k tokens: four facts planted in a long public-domain text, three questions, one needs two facts combined | exact answers |
| A TTL + LRU cache class (about 50 lines) | unit tests with an injected clock |
| An arithmetic evaluator without `eval` (about 120 lines) | unit tests, including malformed input |
| A multi-step agent fix: two bugs and one unimplemented method in a small project, using `list_files`, `read_file`, `write_file`, `run_tests` tools, up to 20 turns, tests are read-only | the project's own tests pass and the test file is unchanged |

Every run uses temperature 0. Pass `--think off on` to run each model both ways.

## Run it

```bash
./fetch-data.sh                      # Moby-Dick from Project Gutenberg, used as the long document
python3 bench.py --haystack data/moby-dick.txt \
  --models ornith-1.5:9b qwen3.8:27b --think off on --out results.json
```

You need Python 3.10+ and a running Ollama with the models pulled. Set `OLLAMA_URL` if Ollama isn't on `127.0.0.1:11434`. The bench runs model-written code in a subprocess with a timeout, so use a machine where that's acceptable.

## Our results (Nov 18, 2026, one RTX 3090 24 GB, Ollama Q4_K_M builds)

| Model | Thinking | Short | Long | Median tok/s | Agent fix |
| - | - | - | - | - | - |
| ornith-1.5:9b | off | 9/10 | 4/6 | 114 | pass, 12 s |
| ornith-1.5:9b | on | 9/10 | 5/6 | 104 | pass, 27 s |
| qwen3.8:27b | off | 8/10 | 5/6 | 85 | pass, 19 s |
| qwen3.8:27b | on | 9/10 | 6/6 | 69 | pass, 74 s |

Raw output: `results/2026-11-18.json` and `results/2026-11-18.log`. Write-up: [Ornith 9B vs Qwen 27B on One RTX 3090](https://aceteam.ai/company/blog/ornith-9b-vs-qwen-27b-rtx-3090).

## Add your rig

Run the benchmarks on your hardware and send us the numbers, from one GPU, four GPUs, a Mac Studio or a laptop.

1. Run the bench with any Ollama models you like.
2. Copy your `results.json` to `results/<date>-<your-rig>.json`, for example `results/2026-11-20-2x4090.json`.
3. Add one row per model to the table below, then open a pull request.

| Rig | Model | Thinking | Short | Long | Median tok/s | By |
| - | - | - | - | - | - | - |
| 1x RTX 3090 24 GB | ornith-1.5:9b | on | 9/10 | 5/6 | 104 | AceTeam |
| 1x RTX 3090 24 GB | qwen3.8:27b | on | 9/10 | 6/6 | 69 | AceTeam |

## Caveats

- One run per task at temperature 0, so treat the results as a smoke test.
- Ten short tasks and six long ones. One failed task for a trivial reason moves a score by 10 to 17 points.
- Speed depends on how much of the model fits in VRAM. On a 24 GB card, Ollama moves part of the 27B model onto the CPU past about 16k tokens of context.

## License

MIT. Moby-Dick is public domain; `fetch-data.sh` downloads it from Project Gutenberg rather than shipping it here.
