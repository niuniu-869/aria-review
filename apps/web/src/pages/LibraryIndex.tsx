/**
 * LibraryIndex.tsx — M1 已将列表功能内嵌到 LibraryView 三栏中
 *
 * 本组件继续由 routes.tsx 挂载为 library/ 的 index 子路由，
 * 但 LibraryView 已直接实现三栏，不再依赖此 Outlet 子组件渲染列表。
 * LibraryIndex 只作为保留的路由占位（空渲染），避免路由报错。
 *
 * 测试中仍引用此组件（routing.test.tsx），返回 null 即可。
 */
export function LibraryIndex() {
  // M1: 列表已内嵌到 LibraryView 三栏，此处空渲染
  return null;
}
