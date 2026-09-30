import { createContext, useContext } from "react";
import type { NodeType } from "./types";

export const CatalogContext = createContext<Record<string, NodeType>>({});
export const useCatalog = () => useContext(CatalogContext);
