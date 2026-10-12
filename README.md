# XML to Mermaid

Tool Python chuyển flowchart XML của [diagrams.net/draw.io](https://www.diagrams.net/) sang cú pháp Mermaid `flowchart`.

Tool hỗ trợ:

- XML draw.io dạng thường và dạng nén.
- Chọn page/diagram theo tên khi một file chứa nhiều page.
- Các shape phổ biến: process, ellipse/start-end, decision, parallelogram, database và hexagon.
- Unicode tiếng Việt và nhãn có HTML như `<br>`.
- Nhãn cạnh được lưu trực tiếp hoặc dưới dạng text rời gần đường nối (`yes`, `no`, ...).
- Suy ra đầu nối bị thiếu từ tọa độ hình học của draw.io.
- Tự chọn A4 ngang cho sơ đồ nhỏ và A4 dọc cho sơ đồ nhiều khối.
- Đường nối bo mềm, đầu/góc tròn và khoảng cách node dễ đọc.
- Dùng bộ định tuyến ELK để đường nối tránh node và hạn chế giao cắt.
- Node gọn, text tự xuống dòng bên trong và cỡ chữ điều chỉnh theo số lượng khối.
- Không cần cài package ngoài; chỉ cần Python 3.10+.

## Giao diện Tkinter

Trên Windows, nhấp đúp `run_gui.bat` hoặc chạy:

```powershell
python .\xml_to_mermaid_gui.py
```

Trong giao diện:

1. Nhấn **Chọn file...** và chọn một hoặc nhiều file `.xml`/`.drawio`.
2. Chọn thư mục đầu ra. Mặc định là thư mục `mermaid_output` nằm cạnh file đầu tiên.
3. Chọn **A4 tự động** để tool tự quyết định trang dọc/ngang; cũng có thể ép **A4 dọc**, **A4 ngang** hoặc dùng **Flowchart tự do** kiểu cũ.
4. Nhấn **Chuyển sang Mermaid**.
5. Chọn từng kết quả để xem và nhấn **Copy code đang xem**, hoặc dùng **Copy tất cả**.

Mỗi diagram/page được lưu thành một file `.mmd`. Nếu file đầu ra đã tồn tại, mặc định tool tạo tên mới như `_2`, `_3` để bảo vệ nội dung cũ. Chọn **Ghi đè file trùng tên** nếu muốn thay thế file cũ.

## Sử dụng

Ghi kết quả ra file:

```powershell
python .\xml_to_mermaid.py "C:\duong-dan\so-do.drawio.xml" -o ".\so-do.mmd"
```

In kết quả ra terminal:

```powershell
python .\xml_to_mermaid.py ".\so-do.xml"
```

Chọn một page và ép hướng sơ đồ:

```powershell
python .\xml_to_mermaid.py ".\so-do.xml" --diagram "Trang-1" --direction LR -o ".\so-do.mmd"
```

Nếu file có nhiều page, tool sẽ liệt kê tên các page và yêu cầu chọn bằng `--diagram`; nhờ vậy một file đầu ra không chứa nhiều khai báo flowchart xung đột nhau.

Xem toàn bộ tùy chọn:

```powershell
python .\xml_to_mermaid.py --help
```

`--infer-distance PX` điều chỉnh khoảng cách tối đa dùng để ghép đầu nối hoặc text rời với cạnh. Mặc định là `60`. Dùng `--no-infer-labels` nếu muốn giữ các text rời thành node độc lập.

## Tùy chỉnh bố cục và đường nối

Mặc định tool dùng bố cục `adaptive`: sơ đồ nhỏ dùng A4 ngang, còn sơ đồ có trên 10 khối chuyển sang A4 dọc. Các node được thu gọn, text được bọc bên trong và ELK dành khoảng trống để định tuyến mũi tên tránh các node. Ví dụ ép A4 ngang:

```powershell
python .\xml_to_mermaid.py ".\so-do.xml" `
  --page-layout landscape `
  -o ".\so-do.mmd"
```

Các lựa chọn `--direction`, `--curve`, `--node-spacing` và `--rank-spacing` áp dụng khi dùng `--page-layout flowchart`:

```powershell
python .\xml_to_mermaid.py ".\so-do.xml" `
  --page-layout flowchart `
  --direction LR `
  --curve rounded `
  --node-spacing 70 `
  --rank-spacing 100 `
  -o ".\so-do.mmd"
```

Nếu nơi hiển thị đang dùng Mermaid cũ và chưa hỗ trợ `rounded`, dùng `--curve basis` để có đường cong mềm tương thích rộng hơn. Với đường gấp khúc vuông, dùng `--curve stepAfter`.

Khi sơ đồ có nhiều khối, Mermaid sẽ thu toàn bộ SVG vào vùng hiển thị. Bạn có thể chủ động phóng to khi xem; tool không tiếp tục làm chữ hoặc node quá lớn chỉ để đọc được ở mức zoom 100%.

## Kiểm thử

```powershell
python -m unittest discover -s tests -v
```

## Giới hạn

Mermaid và draw.io không có tập shape tương đương hoàn toàn. Tool ưu tiên giữ cấu trúc luồng, nội dung và loại shape chính; màu sắc, font, kích thước và vị trí tuyệt đối không được chuyển đổi. Các connector không có đủ đầu mút và không thể suy ra sẽ được bỏ qua kèm cảnh báo trên `stderr`.
