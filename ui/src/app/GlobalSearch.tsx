import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSearch } from "@/api/queries";
import { TypeIcon } from "@/components/TypeIcon";
import { Input } from "@/components/ui/input";
import { cn, explorerPath } from "@/lib/utils";

/** Search nodes by id or name; choosing one opens it on the Overview. */
export function GlobalSearch({ className }: { className?: string }) {
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(text.trim()), 150);
    return () => clearTimeout(t);
  }, [text]);
  const { data: hits = [] } = useSearch(debounced);
  useEffect(() => setActive(0), [debounced]);
  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const choose = (i: number) => {
    const hit = hits[i];
    if (!hit) return;
    navigate(explorerPath(hit.type, hit.id));
    setOpen(false);
    setText("");
  };

  return (
    <div ref={box} className={cn("relative", className)}>
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" aria-hidden />
      <Input
        role="combobox"
        aria-expanded={open && hits.length > 0}
        aria-controls="search-results"
        aria-label="Search suppliers, sites, components"
        placeholder="Search suppliers, sites, components…"
        className="pl-8"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, hits.length - 1));
          else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
          else if (e.key === "Enter") choose(active);
          else if (e.key === "Escape") setOpen(false);
          else return;
          e.preventDefault();
        }}
      />
      {open && debounced && hits.length > 0 && (
        <ul id="search-results" role="listbox" className="absolute top-10 right-0 left-0 z-40 max-h-96 overflow-auto rounded-lg border bg-surface p-1 shadow-lg">
          {hits.map((h, i) => (
            <li
              key={`${h.type}:${h.id}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(i)}
              onMouseEnter={() => setActive(i)}
              className={cn("flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm", i === active && "bg-surface-2")}
            >
              <TypeIcon type={h.type} />
              <span className="truncate">{h.name}</span>
              <span className="ml-auto shrink-0 text-xs text-muted">{h.type} · {h.id}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
