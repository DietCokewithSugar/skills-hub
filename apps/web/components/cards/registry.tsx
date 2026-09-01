"use client";

/**
 * 卡片组件注册表（R5 协议第 4 步：前端按 card.type 从组件注册表取组件渲染）。
 *
 * v1 收敛为 6 种，**不允许 skill 自由定义结构** —— 所以这张表是封闭的。
 * 后端 parse_card_spec 会拒绝第 7 种类型，这里的 exhaustive 检查是同一条
 * 约束在前端的镜像：新增类型时 tsc 会报错，不会静默渲染成空白。
 */

import type { CardSpec } from "@/lib/types";
import { ConfirmCard } from "./ConfirmCard";
import { FilePickCard } from "./FilePickCard";
import { FormCard } from "./FormCard";
import { MultiSelectCard } from "./MultiSelectCard";
import { SelectCard } from "./SelectCard";
import { TableReviewCard } from "./TableReviewCard";

export interface CardProps {
  spec: CardSpec;
  expiresAt?: string;
  busy: boolean;
  serverErrors: Record<string, string>;
  onSubmit: (action: string, values: Record<string, unknown>) => void;
}

export function CardRenderer(props: CardProps) {
  switch (props.spec.type) {
    case "confirm":      return <ConfirmCard {...props} spec={props.spec} />;
    case "select":       return <SelectCard {...props} spec={props.spec} />;
    case "multi_select": return <MultiSelectCard {...props} spec={props.spec} />;
    case "form":         return <FormCard {...props} spec={props.spec} />;
    case "file_pick":    return <FilePickCard {...props} spec={props.spec} />;
    case "table_review": return <TableReviewCard {...props} spec={props.spec} />;
    default: {
      // 新增卡片类型而忘了在这里注册时，tsc 会在这一行报错
      const _exhaustive: never = props.spec;
      return null;
    }
  }
}
