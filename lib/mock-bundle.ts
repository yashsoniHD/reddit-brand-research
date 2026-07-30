/**
 * A fictional bundle, used only by the development demo loader. Pinned to the
 * current BUNDLE_VERSION so the demo always renders what the engine now emits.
 *
 * Nothing here is real: the communities are r/example* names, the posts are
 * invented, and every URL points at a reddit.com/r/example* path that does not
 * exist. Its job is to exercise every branch of the report UI (used and unused
 * communities, truncated samples, notices, every idea format, citations)
 * without a backend. Production only ever renders bundles from /api/*.
 */

import { BUNDLE_VERSION } from "./types";
import type {
  Bundle,
  Citation,
  ContentIdea,
  ItemSentiment,
  RedditItem,
  SentimentKey,
  TrendPoint,
} from "./types";

const SUBS = ["exampledesign", "examplefreelance"] as const;

const POST_SEEDS: Array<[string, string]> = [
  [
    "Landed three clients this quarter without sending a single cold email",
    "Everything came from posting the actual work in public. Process shots, the rejected directions, the invoice conversation. People hire what they can already picture themselves buying.",
  ],
  [
    "Is a niche actually necessary or is that just consultant advice?",
    "I keep hearing pick a niche. I have done branding for a dentist, a fintech and a bakery this year and the bakery paid the most. Genuinely unsure what to specialise in.",
  ],
  [
    "How do you price a rebrand when the client has no budget number in mind?",
    "They asked what it costs. I said it depends on scope, they said give me a range, I panicked and said 4 to 12k. Felt terrible. What do you actually say here?",
  ],
  [
    "Referrals dried up completely this year and I do not know why",
    "Two years of steady word of mouth and then nothing since March. Same quality of work, same clients. Wondering if this is the market or me.",
  ],
  [
    "The proposal template that finally stopped clients ghosting me",
    "One page. Problem, approach, three price tiers, a start date. That is it. Close rate went from maybe a third to over half.",
  ],
  [
    "Anyone else find that posting case studies gets zero engagement but wins work?",
    "My case study posts get four likes and then a DM two weeks later from someone who read the whole thing. The vanity numbers say it failed.",
  ],
  [
    "Client wants unlimited revisions written into the contract",
    "They say it is standard for their industry. It is not standard for mine. How hard do I push back before this becomes a red flag I should walk from?",
  ],
  [
    "Should a two person studio have a personal brand or a studio brand?",
    "We keep flip flopping. The work comes through my personal account but we invoice as the studio and it confuses people.",
  ],
  [
    "What actually happens after you post work on a Tuesday morning",
    "Tracked it for six months. Tuesday and Wednesday mornings consistently beat everything else for saves. Weekend posts do nothing for me.",
  ],
  [
    "Turned down a 20k project last week and I keep second guessing it",
    "Scope was vague, the contact could not name a decision maker, and they wanted to start in four days. Everything said no. Still thinking about the money.",
  ],
  [
    "How many discovery calls do you take before you qualify someone out?",
    "I used to take every call. Now I ask three questions by email first and it has saved me maybe twenty hours a month.",
  ],
  [
    "Nobody warns you that the hardest part is the follow up email",
    "The work is easy. Writing the third follow up to someone who said they were excited and then vanished is the part that grinds me down.",
  ],
  [
    "Small studio owners: what does your week actually look like?",
    "I am at maybe fifteen hours of billable work and twenty five hours of everything else. Wondering if that ratio is normal or if I am doing this badly.",
  ],
  [
    "Raised my rates 40 percent and lost exactly one client",
    "The one I lost was the one taking up half my week. Should have done it eighteen months ago.",
  ],
  [
    "Does anyone have a system for turning finished projects into content?",
    "Every project has ten posts in it and I capture none of them because by the time it ships I am already on the next thing.",
  ],
  [
    "Cold outreach converted at 0.4 percent for me over 250 emails",
    "Posting publicly for the same amount of hours brought in four inbound conversations. Sample size of one studio, but the gap was not close.",
  ],
  [
    "Client asked for the source files after refusing to pay the final invoice",
    "Contract covers this but the conversation is still awful. How do you handle it without burning the relationship completely?",
  ],
  [
    "The single best thing I did was write down why I lost each pitch",
    "Twelve losses later the pattern was obvious. It was never price. It was that I was talking to the wrong person in the building.",
  ],
];

const COMMENT_SEEDS: string[] = [
  "The unlimited revisions thing is a hard no for me. Two rounds included, anything past that is billed hourly and written into the first page of the contract.",
  "Niche down by problem, not by industry. I do launch identities. The client can be a bakery or a bank, the problem is the same shape.",
  "Price ranges are fine as long as you follow them with what moves it up or down. Silence after a number is what kills you.",
  "Referrals dried up for me too this year. Everyone I speak to says the same. I do not think it is you.",
  "Posting the rejected directions gets me more inbound than posting the final work. People want to see the thinking.",
  "Fifteen billable hours out of forty is completely normal for a studio owner. The other twenty five is the business.",
  "I ask for the budget in the intake form. If they leave it blank I send the three tier page before I take a call.",
  "Second the one page proposal. Nobody reads twelve pages. I attach the detail as an appendix nobody opens.",
  "Ghosting is not personal, it is usually that your contact lost an internal argument and is embarrassed to say so.",
  "Tuesday morning posts outperform for me too. I assumed it was a myth until I actually logged it.",
  "Walking from a vague 20k is the right call every time. The vague ones eat 60k of your time.",
  "Studio brand for invoices, personal brand for the feed. Nobody has ever been confused by that in practice.",
  "I keep a folder per project and drop a screenshot in it every time something interesting happens. Content writes itself at the end.",
  "Raised rates 30 percent last year, lost nobody, and now I resent the work less. Do it sooner than feels comfortable.",
  "Write down why you lost. I resisted this for years because it felt like self flagellation. It is the highest value hour of my month.",
  "The follow up email is the job. I have a three touch sequence saved and I stopped feeling weird about it once it was a system.",
  "Cold email works if you have a very specific trigger. Otherwise it is a numbers game you will lose to anyone posting publicly.",
  "Source files after non payment is a legal question not a relationship question. Your contract already answered it.",
];

const DAY_MS = 86400_000;
const WINDOW_END = Date.UTC(2026, 6, 26, 9, 0, 0);

function isoDate(stamp: number): string {
  return new Date(stamp * 1000).toISOString().slice(0, 10);
}

// Demo-only phrase filter: which post and comment positions "matched" the
// fictional "cold outreach" filter, so the render paths that key off
// `phrase_match` and `author` have something to show without a backend.
const PHRASE_MATCH_POST_POSITIONS = new Set([0, 6, 15]);
const PHRASE_MATCH_COMMENT_POSITIONS = new Set([3, 16]);

function makePosts(): RedditItem[] {
  return POST_SEEDS.map(([title, body], position) => {
    const created = Math.floor((WINDOW_END - position * 0.72 * DAY_MS) / 1000);
    const subreddit = SUBS[position % SUBS.length];
    return {
      kind: "post",
      id: `demo_p${position + 1}`,
      subreddit,
      title,
      body,
      score: 412 - position * 17,
      comments: 96 - position * 4,
      url: `https://reddit.com/r/${subreddit}/comments/demo_p${position + 1}/`,
      created_utc: created,
      date: isoDate(created),
      author: `demo_user_${(position % 5) + 1}`,
      link_flair_text: position % 4 === 0 ? "Discussion" : undefined,
      upvote_ratio: Math.round((0.99 - position * 0.01) * 100) / 100,
      phrase_match: PHRASE_MATCH_POST_POSITIONS.has(position),
    };
  });
}

function makeComments(): RedditItem[] {
  return COMMENT_SEEDS.map((body, position) => {
    const created = Math.floor((WINDOW_END - (position * 0.68 + 0.3) * DAY_MS) / 1000);
    const subreddit = SUBS[(position + 1) % SUBS.length];
    return {
      kind: "comment",
      id: `demo_c${position + 1}`,
      subreddit,
      title: "",
      body,
      score: 148 - position * 6,
      url: `https://reddit.com/r/${subreddit}/comments/demo_p${position + 1}/demo_c${position + 1}/`,
      created_utc: created,
      date: isoDate(created),
      author: `demo_user_${(position % 5) + 1}`,
      phrase_match: PHRASE_MATCH_COMMENT_POSITIONS.has(position),
    };
  });
}

const POSTS = makePosts();
const COMMENTS = makeComments();
/** Interleaved the way the engine builds them: posts first, then comments. */
const ITEMS: RedditItem[] = [...POSTS, ...COMMENTS];

function citationsFor(indices: number[]): Citation[] {
  return indices.slice(0, 3).map((index) => {
    const item = ITEMS[index - 1];
    const text = item.title || `${item.body.slice(0, 140).trimEnd()}…`;
    return {
      index,
      text,
      url: item.url,
      date: item.date,
      score: item.score,
      type: item.kind,
      subreddit: item.subreddit,
    };
  });
}

const TOPIC_SEEDS: Array<{ label: string; count: number; finding: string; cites: number[] }> = [
  {
    label: "Pricing conversations and rate increases",
    count: 12,
    finding:
      "The recurring failure is not the number, it is the silence after it. People who name a range and immediately explain what moves it report far fewer stalled deals than people who wait for a reaction.",
    cites: [3, 14, 21],
  },
  {
    label: "Inbound work beating cold outreach",
    count: 10,
    finding:
      "Several studios tracked both channels over the same period and found public posting produced more qualified conversations per hour than cold email, even though the cold email numbers looked busier.",
    cites: [1, 16, 24],
  },
  {
    label: "Scope creep and contract terms",
    count: 8,
    finding:
      "Unlimited revisions is the flashpoint. The advice that lands is two included rounds written on page one, with anything past that billed hourly, rather than a negotiation once the work is underway.",
    cites: [7, 17, 19],
  },
  {
    label: "Qualifying clients before the call",
    count: 7,
    finding:
      "A short written intake before any call is the single most repeated time saver, with people reporting they now decline roughly half of enquiries without ever speaking to them.",
    cites: [11, 23, 28],
  },
  {
    label: "Turning finished projects into content",
    count: 5,
    finding:
      "Almost everyone agrees the raw material exists and almost nobody captures it. The one system that keeps coming up is a per project folder that collects screenshots as the work happens.",
    cites: [15, 31, 9],
  },
];

const IDEA_SEEDS: Array<Omit<ContentIdea, "citations"> & { cites: number[] }> = [
  {
    title: "The three sentences that stop a pricing conversation from stalling",
    hook: "I lost four projects to the same eight seconds of silence.",
    format: "Text post",
    angle:
      "Open with the silence after you name a number, then give the exact three sentences that fill it: the range, what moves it up, what moves it down.",
    rationale:
      "Pricing is the largest topic in the sample and the specific failure people describe is the pause, not the price itself.",
    addresses: "Naming a price and then losing the room",
    cites: [3, 21],
  },
  {
    title: "What 250 cold emails actually returned, in one chart",
    hook: "250 cold emails. One reply worth having. Here is what the same hours did instead.",
    format: "Carousel (PDF)",
    angle:
      "Put the two channels side by side across the same number of hours and let the gap do the arguing. End on the one case where cold outreach still works.",
    rationale:
      "A studio in the sample tracked both channels and posted the numbers; the comparison was the most engaged thread of the window.",
    addresses: "Choosing where to spend business development hours",
    cites: [16, 24],
  },
  {
    title: "Two revisions, written on page one",
    hook: "Unlimited revisions is not standard. It is just unpriced.",
    format: "Text post",
    angle:
      "Reframe unlimited revisions as a pricing problem rather than a trust problem, then show the exact clause wording.",
    rationale:
      "Revision limits generated the most disagreement in the sample, and the people with a written clause reported the calmest projects.",
    addresses: "Scope creep disguised as client service",
    cites: [7, 19],
  },
  {
    title: "The intake form that replaced half my discovery calls",
    hook: "I stopped taking calls with people who would not answer three questions by email.",
    format: "Long-form article",
    angle:
      "Walk through the three questions, what each one screens for, and the polite decline you send when the answers are wrong.",
    rationale:
      "Pre call qualification is the most repeated time saving tactic in the sample, with people describing twenty hours a month recovered.",
    addresses: "Discovery calls that were never going to convert",
    cites: [11, 23],
  },
  {
    title: "Why your case studies feel like they are failing",
    hook: "Four likes. Two weeks later, a five figure DM.",
    format: "Text post",
    angle:
      "Separate engagement from intent, then argue that the case study is a sales document that happens to be posted publicly.",
    rationale:
      "Several people described the same pattern of low engagement followed by direct enquiries, and read it as failure at the time.",
    addresses: "Judging content by likes instead of enquiries",
    cites: [6, 26],
  },
  {
    title: "The losses folder",
    hook: "I wrote down why I lost twelve pitches. It was never the price.",
    format: "Poll",
    angle:
      "Ask people to guess the top reason pitches are lost, then reveal that the pattern in this sample was talking to the wrong person in the building.",
    rationale:
      "The loss log post was the highest scoring thread in the window and its conclusion contradicts the assumption most commenters started with.",
    addresses: "Assuming price is why you lost the work",
    cites: [18, 33],
  },
  {
    title: "Show the rejected directions",
    hook: "The work you did not ship is the work that gets you hired.",
    format: "Carousel (PDF)",
    angle:
      "Three rejected directions per slide, the chosen one last, with one line on why each was wrong for the brief.",
    rationale:
      "Multiple people in the sample said process and rejected work outperformed finished work for inbound enquiries.",
    addresses: "Portfolios that show outcomes but hide thinking",
    cites: [5, 1],
  },
  {
    title: "Fifteen billable hours out of forty",
    hook: "Fifteen hours of design. Twenty five hours of everything else. That is the job.",
    format: "Video script",
    angle:
      "Talk through a real week with the hour count on screen, landing on the reassurance that this ratio is normal for studio owners.",
    rationale:
      "A post asking whether this ratio was normal drew a strong consensus that it is, which is exactly the reassurance the audience is searching for.",
    addresses: "Feeling like you are running the studio badly",
    cites: [13, 29],
  },
  {
    title: "The screenshot folder that writes your content for you",
    hook: "Every project already contains ten posts. You are deleting nine of them.",
    format: "Long-form article",
    angle:
      "Walk a single project from kickoff to delivery, marking each moment worth capturing, and end with the folder structure itself.",
    rationale:
      "Turning finished work into content is a recognised gap in the sample and the folder habit was the only system anyone described.",
    addresses: "Finishing projects with nothing left to post",
    cites: [15, 31],
  },
  {
    title: "Raised rates 40 percent, lost one client",
    hook: "The client I lost was the one eating half my week.",
    format: "Text post",
    angle:
      "Open with the before and after rate, then spend the body on which client actually left and why that was the point.",
    rationale:
      "Two separate rate increase posts in the sample reported the same outcome, which makes the claim credible rather than boastful.",
    addresses: "Fear that raising rates empties the pipeline",
    cites: [14, 30],
  },
];

function makeSentiments(): ItemSentiment[] {
  const pattern: SentimentKey[] = ["pos", "neu", "neg", "neu", "pos", "neu", "neg", "pos", "neu"];
  return ITEMS.map((_, position) => ({
    i: position + 1,
    s: pattern[position % pattern.length],
  }));
}

function makeTrend(): TrendPoint[] {
  const counts = [1, 2, 3, 2, 4, 3, 5, 2, 1, 3, 4, 2, 3, 1];
  return counts.map((count, position) => {
    const stamp = WINDOW_END - (counts.length - 1 - position) * DAY_MS;
    return [new Date(stamp).toISOString().slice(0, 10), count];
  });
}

const SENTIMENTS = makeSentiments();

function sentimentCounts(): Record<SentimentKey, number> {
  const counts: Record<SentimentKey, number> = { pos: 0, neu: 0, neg: 0 };
  for (const entry of SENTIMENTS) counts[entry.s] += 1;
  return counts;
}

function sentimentPercentages(counts: Record<SentimentKey, number>): Record<SentimentKey, number> {
  const total = counts.pos + counts.neu + counts.neg;
  const exact = {
    pos: (counts.pos * 100) / total,
    neu: (counts.neu * 100) / total,
    neg: (counts.neg * 100) / total,
  };
  const floors = {
    pos: Math.floor(exact.pos),
    neu: Math.floor(exact.neu),
    neg: Math.floor(exact.neg),
  };
  let remainder = 100 - (floors.pos + floors.neu + floors.neg);
  const ranked = (Object.keys(exact) as SentimentKey[]).sort(
    (a, b) => exact[b] - floors[b] - (exact[a] - floors[a]),
  );
  for (const key of ranked) {
    if (remainder <= 0) break;
    floors[key] += 1;
    remainder -= 1;
  }
  return floors;
}

const COUNTS = sentimentCounts();

export const MOCK_BUNDLE: Bundle = {
  bundle_version: BUNDLE_VERSION,
  generated_at: "2026-07-26 09:14 UTC",
  generated_ts: Math.floor(WINDOW_END / 1000),
  elapsed_seconds: 41.6,
  is_sample: true,
  model: "demo-model",
  model_label: "Demo data (no model was called)",
  source: "Demo fixture, not the Arctic Shift archive",
  inputs: {
    query: 'How small design studios win clients without "cold outreach"',
    profile: "I run a two person branding studio for early stage software companies.",
    subreddits: [...SUBS],
    days: 14,
    phrases: ["cold outreach"],
    // A search run, so the demo exercises the retrieval copy and the window row's
    // "not limited" branch. The quoted phrase is what a real search run would have
    // searched for, which is why it appears in both fields.
    retrieval: "search",
    terms: ["cold outreach"],
  },
  discovery: {
    communities: [
      {
        name: "exampledesign",
        reason: "Studio owners trade pricing and client acquisition advice here",
        subscribers: 412_000,
        used: true,
      },
      {
        name: "examplefreelance",
        reason: "Independent designers discuss contracts, scope and rates",
        subscribers: 186_500,
        used: true,
      },
      {
        name: "examplestudio",
        reason: "Small agency operators compare business development approaches",
        subscribers: 24_300,
        used: false,
      },
    ],
    considered: [
      "exampledesign",
      "examplefreelance",
      "examplestudio",
      "examplebranding",
      "exampleagencylife",
      "examplecreatives",
    ],
  },
  notices: ["r/examplestudio had no posts in the last 30 days, so it was left out."],
  posts: POSTS,
  comments: COMMENTS,
  items: ITEMS,
  sample: {
    posts_fetched: 24,
    comments_fetched: 61,
    posts_analysed: POSTS.length,
    comments_analysed: COMMENTS.length,
    items_analysed: ITEMS.length,
    posts_truncated: true,
    comments_truncated: true,
    low_data: false,
    capacity_limited: false,
    selection: {
      method: "relevance+engagement",
      triaged: 85,
      relevance_counts: { "0": 9, "1": 31, "2": 45 },
    },
    filter: {
      phrases: ["cold outreach"],
      mode: "exact",
      posts_matched: PHRASE_MATCH_POST_POSITIONS.size,
      comments_matched: PHRASE_MATCH_COMMENT_POSITIONS.size,
      posts_scanned: 240,
      comments_scanned: 610,
      dropped: ["ok"],
    },
  },
  analysis: {
    executive_summary:
      "The studios winning work in this window are not outbound. They are publishing the parts of the job that usually stay private, pricing out loud, and screening enquiries in writing before any call happens. For your content, the highest value ground is the pricing conversation itself: it is the biggest topic in the sample and the one where people describe a specific, repeatable failure you can solve in a single post.",
    topics: TOPIC_SEEDS.map((seed, position) => ({
      id: position + 1,
      label: seed.label,
      count: seed.count,
      share: Math.round((1000 * seed.count) / ITEMS.length) / 10,
      finding: seed.finding,
      citations: citationsFor(seed.cites),
    })),
    pain_points: [
      {
        text: "Naming a price and then losing the conversation to the silence that follows",
        citations: citationsFor([3, 21]),
      },
      {
        text: "Referral volume falling away with no change in the work or the clients",
        citations: citationsFor([4, 22]),
      },
      {
        text: "Unlimited revisions arriving as a contract demand rather than a negotiation",
        citations: citationsFor([7, 19]),
      },
      {
        text: "Spending most of the week on work that cannot be billed to anyone",
        citations: citationsFor([13, 29]),
      },
      {
        text: "Finishing a project with nothing captured that could become content",
        citations: citationsFor([15, 31]),
      },
    ],
    questions: [
      {
        text: "Is picking a niche genuinely necessary, or is it advice that only suits consultants?",
        citations: citationsFor([2, 20]),
      },
      {
        text: "What do you actually say when a client asks for a price range?",
        citations: citationsFor([3, 21]),
      },
      {
        text: "Should a two person studio lead with a personal brand or a studio brand?",
        citations: citationsFor([8, 30]),
      },
      {
        text: "How many discovery calls should you take before qualifying someone out?",
        citations: citationsFor([11, 23]),
      },
    ],
    content_ideas: IDEA_SEEDS.map(({ cites, ...idea }) => ({
      ...idea,
      citations: citationsFor(cites),
    })),
    item_sentiments: SENTIMENTS,
  },
  stats: {
    sentiment: {
      counts: COUNTS,
      percentages: sentimentPercentages(COUNTS),
      classified: SENTIMENTS.length,
      unclassified: 0,
    },
    engagement: {
      score: POSTS.reduce((total, post) => total + post.score, 0),
      comments: POSTS.reduce((total, post) => total + (post.comments ?? 0), 0),
    },
    date_range: {
      oldest: "2026-07-13",
      newest: "2026-07-26",
      newest_ts: Math.floor(WINDOW_END / 1000),
      newest_iso: "2026-07-26 09:00 UTC",
      label: "13 to 26 Jul 2026",
      short_label: "13 to 26 Jul",
    },
    lag_hours: 0.2,
    trend: makeTrend(),
  },
};
