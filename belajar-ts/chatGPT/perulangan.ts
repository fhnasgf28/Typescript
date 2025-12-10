console.log("perulangan for")
for (let i: number = 0; i < 10; i++) {
    console.log(`Iterasi ke- ${i + 1}`);
}

//contoh 2
console.log("perulangan for of untuk nilai dalam array")
const fruits: string[] = ["Apel", "Jeruk", "Mangga", "Durian"];
for (const fruit of fruits) {
    console.log(`Buah : ${fruit}`)
}

const user = {
    name: "Farhan",
    age: 25,
    city: "Cirebon"
}

for (const key in user){
//     memastikan property milik objek itu sendiri, bukan dari prototype chain
    if (Object.prototype.hasOwnProperty.call(user, key)) {
        const value = user[key as keyof typeof user];
        console.log(`${key}: ${value}`)
    }
}

//contoh 4 perulangan menggunakan while
console.log("Perulangan while");
let counter: number = 0;
while (counter < 3) {
    console.log(`counter while: ${counter}`)
    counter++;
}

// contoh ke 5 for each
console.log("Perulangan for each")
const colors: string[] = ["Merah", "Kuning", "Hijau", "Biru"];
colors.forEach((color, index) => {
    console.log(`Warna ke-${index + 1}: ${color}`);
})