export function Hero() {
  return (
    <div className="flex flex-col items-start gap-5">
      <div className="flex items-center gap-2.5">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo.svg" alt="" width={32} height={32} className="size-8" />
        <span className="h-quiet">Reddit Content Ideas</span>
      </div>

      <h1 className="max-w-2xl text-3xl leading-[1.15] font-semibold tracking-tight text-balance text-foreground sm:text-[2.5rem]">
        Turn what people actually say on Reddit into posts you can publish.
      </h1>

      <p className="max-w-xl text-base leading-relaxed text-muted-foreground">
        Type what you are looking for. We find the communities, read them, and hand back
        content ideas with the hook written and the evidence linked. Click any idea and
        get the full post.
      </p>
    </div>
  );
}
