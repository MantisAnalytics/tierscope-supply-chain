import { useEffect, useRef, useState } from "react";
import { useSearch } from "@/api/queries";
import { TypeIcon } from "@/components/TypeIcon";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** Pick a node of a type by searching its id or name. */
export function NodePicker({ type, value, onChange, id, placeholder }: {
  type?: string;
  value: string;
  onChange: (id: string) => void;
  id?: string;
  placeholder?: string;
}) {
  const [text, setText] = useState(value);
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  useEffect(() => setText(value), [value]);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(text.trim()), 150);
    return () => clearTimeout(t);
  }, [text]);
  const { data: hits = [] } = useSearch(debounced, type);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  return (
    <div ref={box} className="relative">
      <Input
        id={id}
        value={text}
        placeholder={placeholder ?? (type ? `Search ${type} by id or name` : "Search by id or name")}
        onChange={(e) => {
          setText(e.target.value);
          onChange(e.target.value.trim());
          setOpen(true);
        }}
        onKeyDown={(e) => e.key === "Escape" && open && (e.stopPropagation(), setOpen(false))}
        autoComplete="off"
      />
      {open && debounced && hits.length > 0 && (
        <ul role="listbox" className="absolute top-10 right-0 left-0 z-50 max-h-56 overflow-auto rounded-md border bg-surface p-1 shadow-lg">
          {hits.map((h) => (
            <li
              key={`${h.type}:${h.id}`}
              role="option"
              aria-selected={h.id === value}
              className={cn("flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-surface-2")}
              onMouseDown={(e) => {
                e.preventDefault();
                setText(h.id);
                onChange(h.id);
                setOpen(false);
              }}
            >
              <TypeIcon type={h.type} />
              <span className="truncate">{h.name}</span>
              <span className="ml-auto text-xs text-muted">{h.id}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
