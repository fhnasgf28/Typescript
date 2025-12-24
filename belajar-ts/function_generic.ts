// fungsi dengan tipe parameter dan pengembalian

function sum(a: number, b: number): number {
    return a + b;
}

//generic function
function wrapValue<T>(value: T): T[] {
    return [value]
}

console.log(sum(10, 20))
console.log(wrapValue("Hello"))
console.log(wrapValue("123"))