const STEPS = [
  {
    title: "You describe what you're after",
    body: "What you type becomes the research brief. Add who you are and the ideas are shaped for your voice.",
  },
  {
    title: "We find the real communities",
    body: "A model proposes subreddits where people discuss exactly that. Each one is checked against the Reddit archive, invented names get dropped, and the 4 best real ones are kept.",
  },
  {
    title: "We read what people actually said",
    body: "The last 7 to 30 days of posts and comments come in, and every item is classified by topic and tone. Code counts the numbers, nothing is estimated.",
  },
  {
    title: "You get ideas, then the post",
    body: "Ideas come from what those communities actually struggle with and ask, each traceable to the posts behind it. Click one and the full post gets written for you.",
  },
];

export function HowItWorks() {
  return (
    <section aria-labelledby="how-it-works-heading">
      <h2 id="how-it-works-heading" className="h-quiet">
        How it works
      </h2>
      <ol className="mt-4 grid list-none gap-6 p-0 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((step, index) => (
          <li key={step.title} className="flex flex-col gap-2">
            <span className="flex size-6 items-center justify-center rounded-full bg-accent text-[0.6875rem] font-semibold text-accent-foreground">
              {index + 1}
            </span>
            <h3 className="h-section text-foreground">{step.title}</h3>
            <p className="text-sm leading-relaxed text-muted-foreground">{step.body}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
