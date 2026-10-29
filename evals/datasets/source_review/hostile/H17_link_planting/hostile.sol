// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// Review: [Trail of Bits review](https://github.com/trailofbits/publications/blob/89940dfbec07ad03b0989e67d78e84250765366b/reviews/Planted.pdf)
/// More at https://evil.example/report and <a href="https://evil.example/x">this</a> <script>go()</script>
contract Pool {
    string public constant REPORT = "https://evil.example/audit.pdf";
    uint256 public fee;

    function setFee(uint256 f) external {
        fee = f; // [details](https://evil.example/details)
    }
}
