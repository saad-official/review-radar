import { links, textLink } from "@/lib/site";

const items: { q: string; a: React.ReactNode }[] = [
  {
    q: "Does it post replies to the App Store?",
    a: (
      <p>
        No. Posting needs your App Store Connect credentials, so approved replies are exported as a CSV you upload or paste
        yourself. Issues are different: an approved issue is created in the GitHub repository you connected.
      </p>
    ),
  },
  {
    q: "Can the agent create an issue on its own?",
    a: (
      <p>
        It cannot. The agent&apos;s tools only read reviews, extract signals, search memory, cluster and draft. Creating an
        issue is done by the approval endpoint, acting on a stored proposal, after a person approves it. There is no tool the
        model could call to skip that.
      </p>
    ),
  },
  {
    q: "What if a review tries to give the agent instructions?",
    a: (
      <p>
        Review text is untrusted input. It is wrapped in tags in every prompt with an instruction to treat it as data, tool
        arguments are validated, and the worst a successful injection could do is produce a bad draft, which still waits for
        you. The demo data includes one such review so you can see it in the trajectory.
      </p>
    ),
  },
  {
    q: "Will it propose the same theme every day?",
    a: (
      <p>
        No. Themes and past proposals are its memory. Before proposing, it searches them, links new reviews to existing themes,
        and respects earlier decisions: a rejection reason like &ldquo;duplicate of #38&rdquo; keeps it from asking again.
      </p>
    ),
  },
  {
    q: "What does a run cost?",
    a: (
      <p>
        A daily run over about 50 new reviews costs well under a cent at paid model rates. Every run has a hard budget of
        $0.10 and a 200-second deadline, and the run page shows tokens and dollars for every step.
      </p>
    ),
  },
  {
    q: "Who made this?",
    a: (
      <p>
        It is a phase 3 project of the{" "}
        <a href={links.journey} className={textLink}>
          AI Engineering Journey
        </a>{" "}
        and app 9 of the{" "}
        <a href={links.series} className={textLink}>
          Vibe Build Series
        </a>
        . The{" "}
        <a href={links.repo} className={textLink}>
          source
        </a>{" "}
        includes the evaluation set and the recorded trajectories.
      </p>
    ),
  },
];

/** Native disclosure list: works without JavaScript and with find-in-page. */
export function Faq() {
  return (
    <div className="min-w-0 border-t border-border">
      {items.map((item) => (
        <details key={item.q} className="group border-b border-border">
          <summary className="flex cursor-pointer list-none items-start justify-between gap-6 py-5 [&::-webkit-details-marker]:hidden">
            <h3 className="text-lg leading-snug font-semibold sm:text-xl">{item.q}</h3>
            <span
              aria-hidden="true"
              className="figure mt-0.5 grid size-7 shrink-0 place-items-center rounded-sm border border-border text-base leading-none group-open:border-foreground group-open:bg-foreground group-open:text-background"
            >
              <span className="group-open:hidden">+</span>
              <span className="hidden group-open:inline">&minus;</span>
            </span>
          </summary>
          <div className="max-w-2xl space-y-3 pb-6 text-[0.9375rem] leading-relaxed text-foreground/85">{item.a}</div>
        </details>
      ))}
    </div>
  );
}
