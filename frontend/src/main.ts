import './styles/fonts.css';
import './styles/index.css';
import { bootstrap, destroy } from './app/bootstrap';
import { installErrorReporting } from './app/error-reporting';
import { installShell } from './app/shell';
import { installUnloadCleanup } from './shared/operations';

// 未处理异常捕获必须最先安装，随后安装共享 shell，再启动领域 controller。
installErrorReporting();
installUnloadCleanup();
installShell();

bootstrap();
window.addEventListener('pagehide', destroy, { once: true });
