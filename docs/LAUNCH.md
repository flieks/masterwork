# Launch checklist

Everything here is in the order it has to happen. A step marked *you* needs a
login or a public action only the maintainer should take.

## 1. Publish to npm (*you*)

The name `masterwork` was published once and unpublished on 2026-08-13. The
README's only install path is `npx masterwork`, so nothing else on this list
works until the package is back.

```bash
npm login
npm publish            # version in package.json must be new, 0.2.1 or later
npx masterwork@latest  # in an empty folder, on a fresh shell: the real cold-start test
```

The README images use absolute GitHub URLs so they render on npmjs.com.

## 2. Tag, release, Homebrew tap (*you*)

```bash
git tag v0.2.1 && git push origin v0.2.1
gh release create v0.2.1 --generate-notes
scripts/brew-formula.sh v0.2.1   # prints the formula with the tarball sha256 filled in
```

Then create the tap repo `flieks/homebrew-masterwork` and commit the printed
formula as `Formula/masterwork.rb`. Users install with:

```bash
brew install flieks/masterwork/masterwork
```

## 3. Site

Live at https://masterwork-site.vercel.app (Vercel project `masterwork-site`,
team `itsource`). Source in `site/`, deploy with:

```bash
cd site && vercel deploy --prod --yes --scope itsource
```

A custom domain is a one-liner once bought (`vercel domains add <domain> --scope itsource`).
When a demo GIF exists, put it in `site/assets/` and swap it for the hero image.

## 4. Quiet channels first

- GitHub topics: done (`claude-code`, `claude-skills`, `agent-skills`, `codex`, `skills`, `ai-agents`, `developer-tools`, `observability`).
- Set the repo's website field to the site URL: `gh repo edit --homepage https://masterwork-site.vercel.app`.
- awesome-claude-code: submissions go through their web issue form only, never a PR or `gh`
  (https://github.com/hesreallyhim/awesome-claude-code/issues/new?template=recommend-resource.yml).
  Their CONTRIBUTING says they favour projects that already have users, so file this
  a week or two after Show HN. One-line description, no pitch, no emoji:

  > A local web app that browses, edits and installs the skills and subagents in ~/.claude, ~/.codex and ~/.agents, runs scored simulations of a skill against a scenario with a capability checklist, audits skills for overfitting, and records which skills each Claude Code session used, with time and cost.

## 5. Show HN and Reddit, same morning (*you*)

Tuesday to Thursday, 9 to 12 ET. Post from your own account, reply to every
comment in the first hour, never ask anyone to upvote.

**Show HN title** (no caps, no exclamation, under 80 chars):

> Show HN: Masterwork – test whether your Claude Code skills actually work

**Show HN first comment** (post it right after submitting, plain text):

> I write a lot of SKILL.md files for Claude Code and kept running into the same problem: I had no idea whether a skill fired when it should, or whether it only worked on the one example I wrote it against. So I built a local tool that runs a skill against a scenario and grades the result on a checklist, then keeps the score so I can see whether an edit helped.
>
> It also lists every skill across ~/.claude, ~/.codex and the shared ~/.agents folder and shows which agent actually loads each copy, because I had 23 duplicated skills before I checked. Catalog install from skills.sh and GitHub, chat-based edits that come back as a diff you accept or reject, and session recording so a skill's score sits next to how often it really ran and what it cost.
>
> Runs on your own machine through the claude CLI, so it uses your existing subscription and no API key. SQLite, nothing leaves your disk. npx masterwork to try it. Codex session support and a GitHub sync for the skills folder are next. Happy to answer anything about how the scoring works.

**r/ClaudeAI and r/ClaudeCode** (check each sidebar for a self-promotion rule or a showcase day first):

Title:

> I built a local tool that scores your Claude Code skills against real scenarios, so you know they work before you rely on them

Body:

> Writing a SKILL.md is easy. Knowing it actually fires and does the right thing is the part I kept getting wrong. Masterwork runs a skill against a scenario, grades it on a checklist, and shows exactly which criterion it missed. Re-run after the edit and the score tells you if the fix held.
>
> Other things it does: lists every skill across ~/.claude, ~/.codex and ~/.agents and says which agent loads each one (found a lot of duplicates that way), installs from skills.sh and GitHub, edits by chat with diffs you accept or reject, and records your sessions so you see which skills ran, how long they took and what they cost.
>
> Local only, runs through your claude CLI on your existing subscription. npx masterwork. Source and screenshots: https://github.com/flieks/masterwork. Would love to hear what you would want scored.

## 6. After launch

- Watch the issues tab for the first week and answer within the day.
- Newsletters (Latent Space, TLDR AI) pick up from HN traction, there is no form to pitch them.
- Product Hunt only if the HN post shows there is an audience, it is a four-week project on its own.
