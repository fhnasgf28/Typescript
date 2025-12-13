// Tantangan 1
let username: string = "Budi";
let age: number = 25;
let isAdmin: boolean = true;
let skills: string[] = ["JavaScript", "Python", "PHP"];

function introduce(name: string, age: number): string {
    return `Halo, nama saya ${name} dan umur saya ${age}`;
}

// Tantangan 2
interface Product {
    id: number;
    name: string;
    price: number;
    description?: string; // Tanda tanya (?) membuatnya opsional
}