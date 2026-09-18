/**
 * 破坏性操作的二次确认入口。
 *
 * legacy 用了 20 多处 `window.confirm`。阶段 4 保留 confirm 的**同步阻塞语义**（改成
 * 自绘异步弹窗会改变调用点的时序，属于视觉/交互改版，不在本阶段范围内），但把它
 * 收敛到一个可注入、可测试的入口：领域代码不再直接摸 `window.confirm`，测试也不必
 * 再 stub 全局对象。
 */

export interface ConfirmRequest {
  message: string;
  /** 破坏性操作的额外说明，会拼在 message 之后。 */
  detail?: string;
}

export type Confirmer = (request: ConfirmRequest) => boolean;

export function createConfirmer(prompt: (text: string) => boolean = defaultPrompt): Confirmer {
  return ({ message, detail }) => prompt(detail ? `${message}\n\n${detail}` : message);
}

function defaultPrompt(text: string): boolean {
  return window.confirm(text);
}

/** 测试用：总是同意 / 总是拒绝。 */
export const alwaysConfirm: Confirmer = () => true;
export const neverConfirm: Confirmer = () => false;
