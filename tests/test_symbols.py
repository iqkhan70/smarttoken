from smarttoken.pack import _hit_windows
from smarttoken.symbols import _regex_symbols, extract_symbols, symbols_matching

GO_SRC = """package order

type Service struct {
\trepo *Repository
}

func (s *Service) AddItem(ctx context.Context, userID string, itemID int) error {
\tcart, err := s.repo.Cart(ctx, userID)
\tif err != nil {
\t\treturn err
\t}
\treturn s.repo.AddItem(ctx, cart.ID, itemID)
}

func (s *Service) ClearCart(ctx context.Context, userID string) error {
\treturn s.repo.Clear(ctx, userID)
}
"""

CS_SRC = """namespace Eats.Payments;

public interface IPaymentService
{
    Task<string> CreatePaymentIntentAsync(int orderId);
    Task RefundAsync(int orderId);
}
"""


def test_go_methods_have_ranges() -> None:
    by_name = {s.name: s for s in extract_symbols(GO_SRC, "go", "internal/order/service.go")}
    assert by_name["AddItem"].kind == "method"
    assert (by_name["AddItem"].line, by_name["AddItem"].end_line) == (7, 13)
    assert by_name["Service"].kind == "type"


def test_tsx_arrow_components_are_symbols() -> None:
    src = "export const CartScreen = () => {\n  return <View />;\n};\n"
    names = [s.name for s in extract_symbols(src, "typescript", "app/cart.tsx")]
    assert names == ["CartScreen"]


def test_regex_fallback_has_no_ranges() -> None:
    hits = _regex_symbols("def verify(sig):\n    return sig\n", "python")
    assert [(h.name, h.line, h.end_line) for h in hits] == [("verify", 1, 1)]


def test_symbols_matching_uses_task_words() -> None:
    names = [s.name for s in symbols_matching(GO_SRC, "go", "add an item to the cart", "service.go")]
    assert "AddItem" in names and "ClearCart" in names


def test_window_is_whole_enclosing_function() -> None:
    symbols = extract_symbols(GO_SRC, "go", "service.go")
    windows = _hit_windows(GO_SRC, "clear the cart", 12, symbols)
    starts = [(start, end, why) for start, end, _, why in windows]
    assert (15, 17, "method `ClearCart`") in starts


def test_one_line_declaration_borrows_parent() -> None:
    symbols = extract_symbols(CS_SRC, "csharp", "PaymentService.cs")
    windows = _hit_windows(CS_SRC, "refund", 12, symbols)
    assert [(start, end) for start, end, _, _ in windows] == [(3, 7)]
    assert windows[0][3] == "interface `IPaymentService`"
