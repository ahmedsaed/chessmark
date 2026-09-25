"use client";

/**
 * The archive's filters (UI-12).
 *
 * **A real `<form method="get">` first, and a client component second.** With JavaScript off it
 * submits to `/games` like any form, and the page normalises the address it lands on. With it on,
 * a change to a select applies at once and the search applies on Enter, as a client navigation to
 * the same canonical URL `archiveHref` writes — so the two paths cannot disagree about what a
 * filter's address is.
 *
 * **Search applies on submit, not per keystroke.** Every distinct query is a server read and a cache
 * entry; nine keystrokes would be nine of each, eight of them for words nobody meant.
 * `ModelTable` filters as you type because it holds the whole catalogue in memory — the archive
 * grows without bound and is never sent whole.
 */

import { useRouter } from "next/navigation";
import type { FormEvent } from "react";

import {
  ENDINGS,
  endingLabel,
  isFiltered,
  parseArchive,
  withFilter,
  type ArchiveFilter,
} from "@/lib/archive";

export interface ModelOption {
  id: string;
  name: string;
}

export interface EventOption {
  slug: string;
  name: string;
}

export function ArchiveFilters({
  filter,
  models,
  events,
}: {
  filter: ArchiveFilter;
  models: ModelOption[];
  events: EventOption[];
}) {
  const router = useRouter();

  function apply(form: HTMLFormElement) {
    const params: Record<string, string> = {};
    new FormData(form).forEach((value, key) => {
      if (typeof value === "string") params[key] = value;
    });
    router.push(withFilter(parseArchive(params), {}));
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    apply(event.currentTarget);
  }

  const onPick = (event: FormEvent<HTMLSelectElement>) => {
    if (event.currentTarget.form) apply(event.currentTarget.form);
  };

  return (
    <form
      action="/games"
      method="get"
      role="search"
      aria-label="Filter games"
      onSubmit={onSubmit}
      className="flex flex-col gap-4 border border-line bg-surface p-4"
    >
      <div className="flex gap-2">
        <input
          type="search"
          name="q"
          defaultValue={filter.q ?? ""}
          maxLength={100}
          placeholder="Search a model or a player…"
          aria-label="Search by model or player"
          className="min-w-0 flex-1 border border-line bg-ground px-3 py-2 font-mono text-sm text-ink placeholder:text-ink-faint focus:border-accent-dim focus:outline-none"
        />
        <button
          type="submit"
          className="flex-none border border-accent-deep bg-accent px-4 py-2 font-mono text-meta uppercase tracking-[0.14em] text-on-accent transition-colors hover:bg-accent/90"
        >
          Search
        </button>
      </div>

      {/* **On a phone the filters fold away behind a toggle; from `sm` they are always shown.**
          Eight stacked controls filled the first screen of a 375px viewport, so the page opened
          on a form with not one game in sight. A checkbox and `peer-checked` rather than state:
          it has to work in the no-JavaScript path this form exists to keep. `!` because `globals.css` themes every checkbox unlayered, which outranks a utility. It starts open when a
          filter is already applied, so what is narrowing the list is never hidden. */}
      <input
        type="checkbox"
        id="archive-more"
        defaultChecked={isFiltered(filter)}
        className="peer sr-only! sm:hidden!"
      />
      <label
        htmlFor="archive-more"
        className="cursor-pointer select-none font-mono text-meta uppercase tracking-[0.14em] text-accent peer-focus-visible:underline sm:hidden"
      >
        Filters
      </label>

      <div className="hidden flex-col gap-4 peer-checked:flex sm:flex">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Select label="Show" name="show" value={filter.show} onChange={onPick}>
            <option value="played">Played</option>
            <option value="live">Live</option>
            <option value="finished">Finished</option>
            <option value="aborted">Aborted</option>
            <option value="all">Everything</option>
          </Select>
          <Select label="Result" name="result" value={filter.result ?? ""} onChange={onPick}>
            <option value="">Any</option>
            <option value="white">White won</option>
            <option value="black">Black won</option>
            <option value="draw">Draw</option>
          </Select>
          <Select label="Ending" name="ending" value={filter.ending ?? ""} onChange={onPick}>
            <option value="">Any</option>
            {ENDINGS.map((ending) => (
              <option key={ending} value={ending}>
                {endingLabel(ending)}
              </option>
            ))}
          </Select>
          <Select label="Players" name="players" value={filter.players ?? ""} onChange={onPick}>
            <option value="">Anyone</option>
            <option value="models">Model vs model</option>
            <option value="humans">A person played</option>
          </Select>
          <Select label="Ranked" name="ranked" value={filter.ranked ?? ""} onChange={onPick}>
            <option value="">Either</option>
            <option value="ranked">Ranked</option>
            <option value="unranked">Unranked</option>
          </Select>
          <Select label="Sort" name="sort" value={filter.sort} onChange={onPick}>
            <option value="newest">Newest</option>
            <option value="longest">Longest</option>
            <option value="costliest">Costliest</option>
          </Select>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Model">
            <input
              name="model"
              list="archive-models"
              defaultValue={filter.model ?? ""}
              placeholder="any model"
              className={CONTROL}
            />
          </Field>
          <Field label="Against">
            <input
              name="vs"
              list="archive-models"
              defaultValue={filter.vs ?? ""}
              placeholder="any opponent"
              className={CONTROL}
            />
          </Field>
          <Select label="Event" name="event" value={filter.event ?? ""} onChange={onPick}>
            <option value="">Any, or none</option>
            {events.map((event) => (
              <option key={event.slug} value={event.slug}>
                {event.name}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {/* One list for both inputs. Suggestions only: the field takes any id, and an id that played
          nothing is an empty list, not an error. */}
      <datalist id="archive-models">
        {models.map((model) => (
          <option key={model.id} value={model.id}>
            {model.name}
          </option>
        ))}
      </datalist>
    </form>
  );
}

const CONTROL =
  "w-full min-w-0 border border-line bg-ground px-2 py-2 font-mono text-sm text-ink placeholder:text-ink-faint focus:border-accent-dim focus:outline-none";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="font-mono text-label uppercase tracking-[0.14em] text-ink-faint">
        {label}
      </span>
      {children}
    </label>
  );
}

function Select({
  label,
  name,
  value,
  onChange,
  children,
}: {
  label: string;
  name: string;
  value: string;
  onChange: (event: FormEvent<HTMLSelectElement>) => void;
  children: React.ReactNode;
}) {
  return (
    <Field label={label}>
      {/* The browser's own arrow sits against the right border with no way to pad it, so it is
          switched off and drawn here, inset like the text on the left. */}
      <span className="relative block">
        <select
          name={name}
          defaultValue={value}
          onChange={onChange}
          className={`${CONTROL} appearance-none pr-8`}
        >
          {children}
        </select>
        <svg
          aria-hidden
          viewBox="0 0 10 6"
          className="pointer-events-none absolute top-1/2 right-3 h-1.5 w-2.5 -translate-y-1/2 text-ink-faint"
        >
          <path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      </span>
    </Field>
  );
}
