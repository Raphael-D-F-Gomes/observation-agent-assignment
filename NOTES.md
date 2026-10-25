# Notes

To tackle this assessment I'll follow the standard workflow I use for software engineering problems. The first
part, of course, is to understand the business problem. We want a solution that reads a dataset with emails,
looks for recurring work and derives insights on how to automate work from the dataset. We want to use AI agents
only in the parts of the workflow we can't get a deterministic result.

The first questions that comes to mind are: what we want to search on the emails? what defines a recurring work?
If we want to automate tasks, we should look for work that repeats. If we define categories for kinds of work, we can
measure how they repeat. Also, we can look for references in the messages about recurrence tasks.

Now, we need to define the design of our workflow. We broke it in five parts. Read the corpus, group the messages,
ask an observer what the repeated work is, price what survives, and draft only the few things worth handing to
someone the next morning. The first, the pricing, and the dashboard are Python. The observer and the drafter are
the only model calls, because "this is the same task" is a judgment.

Reading the input is deterministic, so we do it in Python all the way. One row per file, including a bad parse.
A real copy is the same normalized body, the same sender, and the same sent time. The first path in sort order
is the event. The other copies stay stored and are labeled duplicate, so we can show we read them. A thread
comes from In-Reply-To and References when those headers exist. This archive barely has them, so a dated reply
can also join dated mail with the same normalized subject inside 60 days. A message with no date does not join
on subject. From the canonical events we draw links: same subject on different days, the same body on different
dates, the same thread, overlapping people, a cue phrase such as "every day" or "please advise", and token
overlap only when nothing else linked the message. Three or more linked events are a component. Fewer than
three distinct dates is an incident. The rest stay pending until a later step accepts or rejects them. Every
file gets exactly one label.

We do not hand the observer the archive. 3,240 messages do not fit in one context. We hand it one pack, a JSON
file of one component or a slice of one, or of cue hits, or of leftover mail grouped by subject. A pack stays
under 64,000 estimated tokens. A long body is split into overlapping raw-byte spans instead of being truncated.
The pack id is a hash of that payload, the prompt version, and the schema version. A second run against an
unchanged corpus does not call the model again for a pack that already succeeded. On this corpus prepare stored
3,240 messages, 0 parse errors, 2 undated messages, 133 components, and 56 packs, in about 12 seconds. The
projected cold run, with 8 observers at a time, 2 drafters, and one reserved retry wave, stayed under 25 minutes.

The observer returns JSON and does not write a file. We came to that shape by listing what the dashboard has to
prove. A process needs a short title, the member paths, and a span id copied from the pack. The span is how we
get back to a verbatim slice of the raw file. Stalls, a role label, and a stated cadence are optional, and they
use the same span rule. The model also picks minutes per occurrence and an automatable fraction, but only from
ladders: 5, 10, 15, 30, 45, or 60 minutes, and 0.25, 0.5, 0.75, or 1.0. A group that is not repeated work is a
reject, with a reason. The parent writes that JSON under `output/logs/responses/` and records it. Up to 8
observers run at once. Each attempt has 120 seconds and 3 tries.

After that, Python again. A span that is not in the pack, or that no longer matches the raw bytes, does not
ship. If the evidence quote fails, the whole process is dropped. A bad stall or role quote is dropped and the
process can still ship. One event is primary on at most one process. A later claim on the same event is
secondary and stays out of the dollar total. The formula is occurrences divided by months, times minutes over
60, times the fraction, times $85. An occurrence is a distinct UTC date. The month span is at least one.
Published rows are the total. Incidents are listed and excluded. This run published 86 opportunities, about
$2,435 per month, with a sensitivity band of about $1,555 to $3,984 at the neighboring minute rungs.

We did not want a draft of every small thread. The line for an artifact is $1,500 a month. If fewer than five
clear that line, we still draft the five most expensive, and we never draft more than eight. A draft is an SOP,
an email, a checklist, or a template. A factual claim in it has to repeat a quote that opportunity already owns.
Sentences the drafter writes are not evidence. Up to 2 drafters run at once. On this run nothing is close to
$1,500. The largest opportunity is about $143 a month. `draft_targets.json` is empty, marked skipped for an
early serve, and `output/artifacts/` has no files. The report and the dashboard are what this run shipped.

The dashboard binds to 127.0.0.1. The total links to an opportunity, and the opportunity links to the raw file
with the quote marked. If that click is broken, the number should not be on the page.

The model will split one desk habit into two titles, and it will give the same name to two groups that do not
share paths. Both can publish, which is why the report shows the credit-report forward twice. It will also
treat a newsletter that shows up on many days as a process. The quote check is what stops a paraphrase from
becoming evidence. What we cannot check is the minutes and the fraction. Nothing in the mail says the work took
45 minutes, or that three quarters of it can be automated. Those are assumptions. The $85 rate is the rate we
were told to use. Subject threading can also glue two different conversations that share a short subject, or
miss a reply that never said Re.

With another week I would run some tests and check the results to see if they make sense, I would also be interesting to 
try different version of prompts for the observers and drafters and check the results. If we also limit the scope
of our solution we can design more specific prompts that would work better for our dataset. It would also be interesting 
to merge opportunities that are the same work under two titles, check a sample of the
minute guesses against someone who did the job, and only then write the morning-after drafts for the top of
the list.
