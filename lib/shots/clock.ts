// frappe-shots' clocks (docs/app-standards/spec.md §5.5). The demo data and the page are
// both anchored on the demo day in the timezone the screenshots show, so the gap between a
// record and the page's "now" is the same on every machine, whatever the host's own zone:
//
//   - frappe-demo's demo script runs under libfaketime from 09:00 on the demo day, given to
//     it in UTC (`TZ=UTC`), as this file prints it:
//       node clock.ts <YYYY-MM-DD> <HH:MM:SS> <timezone>   →   YYYY-MM-DD HH:MM:SS (UTC)
//   - the page's `Date` starts at noon on the demo day (runner.ts, `clockShim`).

/** The offset of `timeZone` from UTC at the instant `ms`, in milliseconds. */
function offsetAt(ms: number, timeZone: string): number {
	const parts = new Intl.DateTimeFormat("en-US", {
		timeZone,
		hourCycle: "h23",
		year: "numeric",
		month: "2-digit",
		day: "2-digit",
		hour: "2-digit",
		minute: "2-digit",
		second: "2-digit",
	}).formatToParts(new Date(ms));
	const n = (type: string) => Number(parts.find((p) => p.type === type)?.value);
	return Date.UTC(n("year"), n("month") - 1, n("day"), n("hour"), n("minute"), n("second")) - (ms - (ms % 1000));
}

/** The instant (ms since the epoch) at which the wall clock in `timeZone` reads `date` `time`. */
export function zonedTime(date: string, time: string, timeZone: string): number {
	const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
	const t = /^(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(time);
	if (!m || !t) throw new Error(`not a date and a time: ${date} ${time}`);
	const wall = Date.UTC(+m[1], +m[2] - 1, +m[3], +t[1], +t[2], +(t[3] ?? 0));
	// Twice, so an offset that changes between the guess and the answer (a DST day) settles.
	let at = wall - offsetAt(wall, timeZone);
	at = wall - offsetAt(at, timeZone);
	return at;
}

/**
 * A clock shim for every document: the page's `Date` starts at `start` and advances with
 * real time, so "3 days ago" and "today" read the same in every run, whatever today is.
 */
export function clockShim(start: number): string {
	return `(() => {
		const RealDate = Date;
		const offset = ${start} - RealDate.now();
		class ShotDate extends RealDate {
			constructor(...args) { if (args.length === 0) super(RealDate.now() + offset); else super(...args); }
			static now() { return RealDate.now() + offset; }
		}
		globalThis.Date = ShotDate;
	})();`;
}

if (import.meta.main) {
	const [date, time, timeZone] = process.argv.slice(2);
	try {
		if (!date || !time || !timeZone) throw new Error("usage: node clock.ts <YYYY-MM-DD> <HH:MM:SS> <timezone>");
		process.stdout.write(new Date(zonedTime(date, time, timeZone)).toISOString().slice(0, 19).replace("T", " ") + "\n");
	} catch (e) {
		process.stderr.write(`clock.ts: ${e instanceof Error ? e.message : String(e)}\n`);
		process.exit(2);
	}
}
