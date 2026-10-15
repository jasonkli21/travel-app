import type { ReactElement } from "react";

export interface ShoppingProjectChoice {
  project_id: string;
  label: string;
  revision: number;
}

export declare function ShoppingProjectSelect(props: {
  choices: ShoppingProjectChoice[];
  selectedId: string;
  onChange: (projectId: string) => void;
  disabled?: boolean;
}): ReactElement;
