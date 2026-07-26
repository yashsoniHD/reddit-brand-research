const STEPS = [
  {
    title: "Describe what you are looking for",
    body: "What you want content ideas about, and optionally who you are. No subreddit picking.",
  },
  {
    title: "We find and read the communities",
    body: "A model proposes subreddits, each is checked against the Reddit archive, then the last few weeks are read in full.",
  },
  {
    title: "Get platform ready ideas with evidence",
    body: "Eight to twelve ideas for LinkedIn or Instagram, each with a hook, a format and links to the posts behind it.",
  },
];

export function HowItWorks() {
  return (
    <section aria-labelledby="how-it-works-heading">
      <h2
        id="how-it-works-heading"
        className="text-sm font-semibold tracking-wide text-muted-foreground uppercase"
      >
        How it works
      </h2>
      <ol className="mt-4 grid list-none gap-6 p-0 sm:grid-cols-3">
        {STEPS.map((step, index) => (
          <li key={step.title} className="flex flex-col gap-2">
            <span className="flex size-7 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
              {index + 1}
            </span>
            <h3 className="text-sm font-semibold text-foreground">{step.title}</h3>
            <p className="text-sm leading-relaxed text-muted-foreground">{step.body}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
