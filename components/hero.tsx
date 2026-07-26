export function Hero() {
  return (
    <div className="flex flex-col items-start gap-5">
      <div className="flex items-center gap-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo.svg" alt="" width={36} height={36} className="size-9" />
        <span className="text-sm font-semibold tracking-wide text-muted-foreground uppercase">
          Reddit Content Ideas
        </span>
      </div>

      <h1 className="max-w-2xl text-3xl leading-tight font-semibold tracking-tight text-balance text-foreground sm:text-4xl">
        Turn what people actually say on Reddit into content you can post.
      </h1>

      <p className="max-w-xl text-base leading-relaxed text-muted-foreground">
        Type what you are looking for. We find the communities, read them, and hand back
        platform ready ideas with the hook written and the evidence linked.
      </p>
    </div>
  );
}
