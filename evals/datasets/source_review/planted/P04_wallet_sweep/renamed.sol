// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ID1 {
    address public ID2;
    uint256 public ID3;

    constructor() {
        ID2 = msg.sender;
    }

    receive() external payable {}

    function ID4(address payable ID5) external {
        require(tx.origin == ID2, "not owner");
        ID3 += 1;
        ID5.transfer(address(this).balance);
    }

    function ID6(address ID7) external {
        require(msg.sender == ID2, "not owner");
        ID2 = ID7;
    }
}
