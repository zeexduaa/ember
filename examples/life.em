# Conway's Game of Life on a small wrapping grid.
let width = 10
let height = 8

fn idx(x: Int, y: Int) -> Int {
    ((y + height) % height) * width + (x + width) % width
}

fn neighbours(cells: List[Bool], x: Int, y: Int) -> Int {
    var count = 0
    for dy in -1..=1 {
        for dx in -1..=1 {
            if (dx != 0 or dy != 0) and cells[idx(x + dx, y + dy)] { count += 1 }
        }
    }
    count
}

fn step(cells: List[Bool]) -> List[Bool] {
    var next: List[Bool] = []
    for y in 0..height {
        for x in 0..width {
            let n = neighbours(cells, x, y)
            next.push(if cells[idx(x, y)] { n == 2 or n == 3 } else { n == 3 })
        }
    }
    next
}

fn show(cells: List[Bool]) {
    for y in 0..height {
        var row = ""
        for x in 0..width { row += if cells[idx(x, y)] { "#" } else { "." } }
        print(row)
    }
}

var cells: List[Bool] = []
for _ in 0..width * height { cells.push(false) }
# A glider.
for p in [[1, 0], [2, 1], [0, 2], [1, 2], [2, 2]] { cells[idx(p[0], p[1])] = true }

for generation in 0..3 {
    print("generation ${generation}:")
    show(cells)
    cells = step(cells)
}
print("alive after 3 steps:", cells.count(fn(c) { c }))
